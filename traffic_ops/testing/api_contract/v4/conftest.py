#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
This module is used to create a Traffic Ops session and to store prerequisite
data for endpoints.
"""

import json
import logging
import sys
import os
from random import randint
from typing import Any, NamedTuple, Union, Optional, TypeAlias
from urllib.parse import urlparse
import munch

import pytest
import requests

from trafficops.tosession import TOSession
from trafficops.restapi import OperationError

# Create and configure logger
logger = logging.getLogger()

Primitive = Union[bool, int, float, str, None]

JSONData: TypeAlias = Union[dict[str, object], list[object], bool, int, float, Optional[str]]
JSONData.__doc__ = """An alias for the kinds of data that JSON can encode."""

class APIVersion(NamedTuple):
	"""Represents an API version."""
	major: int
	minor: int

	@staticmethod
	def from_string(ver_str: str) -> "APIVersion":
		"""
		Instantiates a new version from a string.

		>>> APIVersion.from_string("4.0")
		APIVersion(major=4, minor=0)
		>>> try:
		... 	APIVersion("not a version string")
		... except ValueError:
		... 	print("whoops")
		...
		whoops
		>>>
		>>> try:
		... 	APIVersion("4.Q")
		... except ValueError:
		... 	print("whoops")
		...
		whoops
		"""
		parts = ver_str.split(".", 1)
		if len(parts) != 2:
			raise ValueError("invalid version; must be of the form '{{major}}.{{minor}}'")
		return APIVersion(int(parts[0]), int(parts[1]))

	def __str__(self) -> str:
		"""
		Coalesces the version to a string.

		>>> print(APIVersion(4, 1))
		4.1
		"""
		return f"{self.major}.{self.minor}"

APIVersion.major.__doc__ = """The API's major version number."""
APIVersion.major.__doc__ = """The API's minor version number."""

class ArgsType(NamedTuple):
	"""Represents the configuration needed to create Traffic Ops session."""
	user: str
	password: str
	url: str
	port: int
	api_version: APIVersion

	def __str__(self) -> str:
		"""
		Formats the configuration as a string. Omits password and extraneous
		properties.

		>>> print(ArgsType("user", "password", "url", 420, APIVersion(4, 0)))
		User: 'user', URL: 'url'
		"""
		return f"User: '{self.user}', URL: '{self.url}'"


ArgsType.user.__doc__ = """The username used for authentication."""
ArgsType.password.__doc__ = """The password used for authentication."""
ArgsType.url.__doc__ = """The URL of the environment."""
ArgsType.port.__doc__ = """The port number on which to connect to Traffic Ops."""
ArgsType.api_version.__doc__ = """The version number of the API to use."""

def pytest_addoption(parser: pytest.Parser) -> None:
	"""
	Parses the Traffic Ops arguments from command line.
	:param parser: Parser to parse command line arguments.
	"""
	parser.addoption(
		"--to-user", action="store", help="User name for Traffic Ops Session."
	)
	parser.addoption(
		"--to-password", action="store", help="Password for Traffic Ops Session."
	)
	parser.addoption(
		"--to-url", action="store", help="Traffic Ops URL."
	)
	parser.addoption(
		"--config",
		help="Path to configuration file.",
		default=os.path.join(os.path.dirname(__file__), "data", "to_data.json")
	)
	parser.addoption(
		"--request-template",
		help="Path to request prerequisites file.",
		default=os.path.join(os.path.dirname(__file__), "data", "request_template.json")
	)
	parser.addoption(
		"--response-template",
		help="Path to response prerequisites file.",
		default=os.path.join(os.path.dirname(__file__), "data", "response_template.json")
	)

def coalesce_config(
	arg: Optional[object],
	file_key: str,
	file_contents: Optional[dict[str, Optional[object]]],
	env_key: str
) -> Optional[str]:
	"""
	Coalesces configuration retrieved from different sources into a single
	string.

	This will raise a ValueError if the type of the configuration value in the
	parsed configuration file is not a string.

	In order of descending precedence this checks the command-line argument
	value, the configuration file value, and then the environment variable
	value.

	:param arg: The command-line argument value.
	:param file_key: The key under which to look in the parsed JSON configuration file data.
	:param file_contents: The parsed JSON configuration file (if one was used).
	:param env_key: The environment variable name to look for a value if one wasn't provided elsewhere.
	:returns: The coalesced configuration value, or 'None' if no value could be determined.
	"""
	if isinstance(arg, str):
		return arg

	if file_contents:
		file_value = file_contents.get(file_key)
		if isinstance(file_value, str):
			return file_value
		if file_value is not None:
			raise ValueError(f"incorrect value; want: 'str', got: '{type(file_value)}'")

	return os.environ.get(env_key)

def parse_to_url(raw: str) -> tuple[APIVersion, int]:
	"""
	Parses the API version and port number from a raw URL string.

	>>> parse_to_url("https://trafficops.example.test:420/api/5.270")
	(APIVersion(major=5, minor=270), 420)
	>>> parse_to_url("trafficops.example.test")
	(APIVersion(major=4, minor=0), 443)
	"""
	parsed = urlparse(raw)
	if not parsed.netloc:
		raise ValueError("missing network location (hostname & optional port)")

	if parsed.scheme and parsed.scheme.lower() != "https":
		raise ValueError("invalid scheme; must use HTTPS")

	port = 443
	if ":" in parsed.netloc:
		port_str = parsed.netloc.split(":")[-1]
		try:
			port = int(port_str)
		except ValueError as e:
			raise ValueError(f"invalid port number: {port_str}") from e

	api_version = APIVersion(4, 0)
	if parsed.path and parsed.path != "/":
		ver_str = parsed.path.lstrip("/api/").split("/", 1)[0]
		if not ver_str:
			raise ValueError(f"invalid API path: {parsed.path} (should be e.g. '/api/4.0')")
		api_version = APIVersion.from_string(ver_str)
	else:
		logging.warning("using default API version: %s", api_version)

	return (api_version, port)

@pytest.fixture(name="to_args")
def to_data(pytestconfig: pytest.Config) -> ArgsType:
	"""
	PyTest fixture to store Traffic ops arguments passed from command line.
	:param pytestconfig: Session-scoped fixture that returns the session's pytest.Config object.
	:returns: Configuration for connecting to Traffic Ops.
	"""
	session_data: JSONData = None
	cfg_path = pytestconfig.getoption("--config")
	if isinstance(cfg_path, str):
		try:
			with open(cfg_path, encoding="utf-8", mode="r") as session_file:
				session_data = json.load(session_file)
		except (FileNotFoundError, PermissionError) as read_err:
			raise ValueError(f"could not read configuration file at '{cfg_path}'") from read_err

	if session_data is not None and not isinstance(session_data, dict):
		raise ValueError(
			f"invalid configuration file; expected top-level object, got: {type(session_data)}"
		)

	to_user = coalesce_config(pytestconfig.getoption("--to-user"), "user", session_data, "TO_USER")
	if not to_user:
		raise ValueError(
			"Traffic Ops password is not configured - use '--to-password', the config file, or an "
			"environment variable to do so"
		)

	to_password = coalesce_config(
		pytestconfig.getoption("--to-password"),
		"password",
		session_data,
		"TO_PASSWORD"
	)

	if not to_password:
		raise ValueError(
			"Traffic Ops password is not configured - use '--to-password', the config file, or an "
			"environment variable to do so"
		)

	to_url = coalesce_config(pytestconfig.getoption("--to-url"), "url", session_data, "TO_USER")
	if not to_url:
		raise ValueError(
			"Traffic Ops URL is not configured - use '--to-url', the config file, or an "
			"environment variable to do so"
		)

	try:
		api_version, port = parse_to_url(to_url)
	except ValueError as e:
		raise ValueError("invalid Traffic Ops URL") from e

	return ArgsType(
		to_user,
		to_password,
		to_url,
		port,
		api_version
	)

@pytest.fixture(name="to_session")
def to_login(to_args: ArgsType) -> TOSession:
	"""
	PyTest Fixture to create a Traffic Ops session from Traffic Ops Arguments
	passed as command line arguments in to_args fixture in conftest.

	:param to_args: Fixture to get Traffic ops session arguments.
	:returns: An authenticated Traffic Ops session.
	"""
	# Create a Traffic Ops V4 session and login
	to_url = urlparse(to_args.url)
	to_host = to_url.hostname
	try:
		to_session = TOSession(
			host_ip=to_host,
			host_port=to_args.port,
			api_version=str(to_args.api_version),
			ssl=True,
			verify_cert=False
		)
		logger.info("Established Traffic Ops Session.")
	except OperationError as error:
		logger.debug("%s", error, exc_info=True, stack_info=True)
		logger.error("Failure in Traffic Ops session creation. Reason: %s", error)
		sys.exit(-1)

	# Login To TO_API
	to_session.login(to_args.user, to_args.password)
	logger.info("Successfully logged into Traffic Ops.")
	return to_session


@pytest.fixture(name="request_template_data", scope="session")
def request_prerequiste_data(pytestconfig: pytest.Config, request: pytest.FixtureRequest
			  ) -> list[Union[dict[str, object], list[object], Primitive]]:
	"""
	PyTest Fixture to store POST request template data for api endpoint.
	:param pytestconfig: Session-scoped fixture that returns the session's pytest.Config object.
	:param request: Fixture to access information about the requesting test function and its fixtures

	:returns: Prerequisite request data for api endpoint.
	"""
	request_template_path = pytestconfig.getoption("--request-template")
	if not isinstance(request_template_path, str):
		# unlike the configuration file, this must be present
		raise ValueError("prereqisites path not configured")

	# Response keys for api endpoint
	data: Union[dict[
		str,
		Union[list[Union[dict[str, object], list[object], Primitive]], dict[object, object], Primitive]
	], Primitive] = None
	with open(request_template_path, encoding="utf-8", mode="r") as prereq_file:
		data = json.load(prereq_file)
	if not isinstance(data, dict):
		raise TypeError(f"request template data must be an object, not '{type(data)}'")
	try:
		request_template = data[request.param]
		if not isinstance(request_template, list):
			raise TypeError(f"Request template data must be a list, not '{type(request_template)}'")
	except AttributeError:
		request_template = data
	return request_template


@pytest.fixture()
def response_template_data(pytestconfig: pytest.Config
			   ) -> dict[str, Union[Primitive, list[
	Union[Primitive, dict[str, object], list[object]]], dict[object, object]]]:
	"""
	PyTest Fixture to store response template data for api endpoint.
	:param pytestconfig: Session-scoped fixture that returns the session's pytest.Config object.
	:returns: Prerequisite response data for api endpoint.
	"""
	prereq_path = pytestconfig.getoption("--response-template")
	if not isinstance(prereq_path, str):
		# unlike the configuration file, this must be present
		raise ValueError("prereqisites path not configured")

	# Response keys for api endpoint
	response_template: Union[dict[
		str,
		Union[list[Union[dict[str, object], list[object], Primitive]], dict[object, object], Primitive]
	], Primitive] = None
	with open(prereq_path, encoding="utf-8", mode="r") as prereq_file:
		response_template = json.load(prereq_file)
	if not isinstance(response_template, dict):
		raise TypeError(f"Response template data must be an object, not '{type(response_template)}'")

	return response_template


def api_response_data(api_response: tuple[Union[Primitive, dict[str, object], list[
	Union[Primitive, dict[str, object], list[
		object]]]], requests.Response], create_check: bool) -> dict[str, object]:
	"""
	Checks API get/post response.
	:param api_response: Raw api response.
	:returns: Verified response data
	"""
	api_data = None
	if isinstance(api_response, tuple):
		api_response = api_response[0]
		if create_check and not isinstance(api_response, munch.Munch):
			raise ValueError("Malformed API response; 'response' property not an munch")
		if not create_check and not isinstance(api_response, list):
			raise ValueError("Malformed API response; 'response' property not an list")
	else:
		raise ValueError("Invalid API response format")

	if api_response:
		try:
			api_data = api_response
			if not create_check:
				api_data = api_response[0]
			if not isinstance(api_data, dict):
				raise ValueError("Malformed API response; 'response' property not a dict")
		except IndexError as e:
			raise ValueError(f"No response data from API request '{e.args[0]}'") from e

	return api_data


def get_existing_object(to_session: TOSession, object_type: str, query_params: Optional[
	dict[str, Any]])-> Union[dict[str, Any], None]:
	"""
	Check if the given endpoint with the given query params already exists.
	:param to_session: Fixture to get Traffic Ops session.
	:param object_type: api call name for get request.
	:param query_params: query params for api get request.
	:returns: Api data for the corresponding api request.
    """
	api_get_response: tuple[Union[dict[str, object], list[
		Union[dict[str, object], list[object], Primitive]], Primitive], requests.Response] = getattr(
		to_session, f"get_{object_type}")(query_params=query_params)
	return api_response_data(api_get_response, create_check = False)


def create_if_not_exists(to_session: TOSession, object_type: str,
			 data: dict[str, Any]) -> Union[dict[str, Any], None]:
	"""
	Hits Post request of the given endpoint with the given data.
	:param to_session: Fixture to get Traffic Ops session.
	:param object_type: api call name for post request.
	:param data: Post data for api post request.
	:returns: Api data for the corresponding api request.
	"""
	api_post_response: tuple[Union[dict[str, object], list[
		Union[dict[str, object], list[object], Primitive]], Primitive], requests.Response] = getattr(
		to_session, f"create_{object_type}")(data=data)
	return api_response_data(api_post_response, create_check = True)


def create_or_get_existing(to_session: TOSession, get_object_type: str, post_object_type: str, data:
	dict[str, Any], query_params: Optional[dict[str, Any]] = None) -> Union[dict[str, Any], None]:
	"""
	Get Api data of the given endpoint with the given query params if it exists. If not, create it.
	:param to_session: Fixture to get Traffic Ops session.
	:param get_object_type: api call name for get request.
	:param post_object_type: api call name for post request.
	:param query_params: query params for api get request.
	:returns: Api data for the corresponding api request.
	@param data: 
	"""
	existing_object = get_existing_object(to_session, get_object_type, query_params)
	return existing_object or create_if_not_exists(to_session, post_object_type, data)


def check_template_data(template_data: Union[list[JSONData], tuple[JSONData, requests.Response]],
						name: str) -> dict[str, object]:
	"""
	Checks API request/response template data.
	:param template_data: Fixture to get template data from a prerequisites file.
	:param name: Endpoint name
	:returns: Verified endpoint data
	"""
	try:
		endpoint = template_data[0]
	except IndexError as e:
		raise TypeError(
			f"malformed  data; no {name} present in {name} array property") from e

	if not isinstance(endpoint, dict):
		raise TypeError(f"malformed data; {name} must be objects, not '{type(endpoint)}'")
	return endpoint


@pytest.fixture(name="cdn_post_data")
def cdn_data_post(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for cdns endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get CDN request template data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	cdn = check_template_data(request_template_data["cdns"], "cdns")

	# Return new post data and post response from cdns POST request
	randstr = str(randint(0, 1000))
	try:
		name = cdn["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		cdn["name"] = name[:4] + randstr
		domain_name = cdn["domainName"]
		if not isinstance(domain_name, str):
			raise TypeError(f"domainName must be str, not '{type(domain_name)}")
		cdn["domainName"] = domain_name[:5] + randstr
	except KeyError as e:
		raise TypeError(f"missing CDN property '{e.args[0]}'") from e

	logger.info("New cdn data to hit POST method %s", cdn)
	# Hitting cdns POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_cdn(data=cdn)
	resp_obj = check_template_data(response, "cdns")
	return resp_obj


@pytest.fixture()
def cache_group_post_data(to_session: TOSession, request_template_data: list[JSONData]
			 ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for cachegroup endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get Cachegroup data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	cache_group = check_template_data(request_template_data["cachegroup"], "cachegroup")
	# Return new post data and post response from cachegroups POST request
	randstr = str(randint(0, 1000))
	try:
		name = cache_group["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		cache_group["name"] = name[:4] + randstr
		short_name = cache_group["shortName"]
		if not isinstance(short_name, str):
			raise TypeError(f"shortName must be str, not '{type(short_name)}")
		cache_group["shortName"] = short_name[:5] + randstr
	except KeyError as e:
		raise TypeError(f"missing Cache group property '{e.args[0]}'") from e

	# Check if type already exists, otherwise create it
	type_data = check_template_data(request_template_data["types"], "types")
	type_object = create_or_get_existing(to_session, "types", "type", type_data,
				      {"useInTable": "cachegroup"})
	cache_group["typeId"] = type_object["id"]

	logger.info("New cachegroup data to hit POST method %s", cache_group)
	# Hitting cachegroup POST method
	response: tuple[JSONData, requests.Response] = to_session.create_cachegroups(data=cache_group)
	resp_obj = check_template_data(response, "cachegroup")
	return resp_obj


@pytest.fixture()
def parameter_post_data(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for parameters endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get CDN request template data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	parameter = check_template_data(request_template_data["parameters"], "parameters")
	# Return new post data and post response from parameters POST request
	randstr = str(randint(0, 1000))
	try:
		name = parameter["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		parameter["name"] = name[:4] + randstr
		value = parameter["value"]
		if not isinstance(value, str):
			raise TypeError(f"value must be str, not '{type(value)}")
		parameter["value"] = value[:5] + randstr
	except KeyError as e:
		raise TypeError(f"missing Parameter property '{e.args[0]}'") from e

	logger.info("New parameter data to hit POST method %s", parameter)
	# Hitting cdns POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_parameter(data=parameter)
	resp_obj = check_template_data(response, "parameter")
	return resp_obj


@pytest.fixture()
def role_post_data(to_session: TOSession, request_template_data: list[JSONData]
			) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for roles endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get role data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	role = check_template_data(request_template_data["roles"], "roles")

	# Return new post data and post response from roles POST request
	randstr = str(randint(0, 1000))
	try:
		name = role["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		role["name"] = name[:4] + randstr
		description = role["description"]
		if not isinstance(description, str):
			raise TypeError(f"description must be str, not '{type(description)}")
		role["description"] = description[:5] + randstr
	except KeyError as e:
		raise TypeError(f"missing Role property '{e.args[0]}'") from e

	logger.info("New role data to hit POST method %s", role)
	# Hitting roles POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_role(data=role)
	resp_obj = check_template_data(response, "role")
	return resp_obj


@pytest.fixture(name="profile_post_data")
def profile_data_post(to_session: TOSession, request_template_data: list[JSONData],
		      cdn_post_data:dict[str, object]) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for profile endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get profile data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	profile = check_template_data(request_template_data["profiles"], "profiles")
	# Return new post data and post response from profiles POST request
	randstr = str(randint(0, 1000))
	try:
		name = profile["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		profile["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing Profile property '{e.args[0]}'") from e

	# Check if cdn already exists, otherwise create it
	profile["cdn"] = cdn_post_data["id"]
	logger.info("New profile data to hit POST method %s", profile)

	# Hitting profile POST method
	response: tuple[JSONData, requests.Response] = to_session.create_profile(data=profile)
	resp_obj = check_template_data(response, "profile")
	return resp_obj


@pytest.fixture(name="tenant_post_data")
def tenant_data_post(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for tenants endpoint.
	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get tenant request template from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""

	tenant = check_template_data(request_template_data["tenants"], "tenants")

	# Return new post data and post response from tenants POST request
	randstr = str(randint(0, 1000))
	try:
		name = tenant["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		tenant["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing tenant property '{e.args[0]}'") from e

	logger.info("New tenant data to hit POST method %s", tenant)
	# Hitting tenants POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_tenant(data=tenant)
	resp_obj = check_template_data(response, "tenant")
	return resp_obj


@pytest.fixture()
def server_capabilities_post_data(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for server_capabilities endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get server_capabilities data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""

	server_capabilities = check_template_data(request_template_data["server_capabilities"],
					   "server_capabilities")

	# Return new post data and post response from server_capabilities POST request
	randstr = str(randint(0, 1000))
	try:
		name = server_capabilities["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		server_capabilities["name"] = name[:3] + randstr
	except KeyError as e:
		raise TypeError(f"missing server_capabilities property '{e.args[0]}'") from e

	logger.info("New server_capabilities data to hit POST method %s", request_template_data)
	# Hitting server_capabilities POST method
	response: tuple[
		JSONData, requests.Response] = to_session.create_server_capabilities(data=server_capabilities)
	resp_obj = check_template_data(response, "server_capabilities")
	return resp_obj


@pytest.fixture(name="division_post_data")
def division_data_post(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for divisions endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get divisions data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""

	division = check_template_data(request_template_data["divisions"], "divisions")

	# Return new post data and post response from division POST request
	randstr = str(randint(0, 1000))
	try:
		name = division["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		division["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing Parameter property '{e.args[0]}'") from e

	logger.info("New division data to hit POST method %s", request_template_data)
	# Hitting division POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_division(data=division)
	resp_obj = check_template_data(response, "divisions")
	return resp_obj


@pytest.fixture(name="region_post_data")
def region_data_post(to_session: TOSession, request_template_data: list[JSONData],
			  division_post_data: dict[str, object]) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for region endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get region data from a prerequisites file.
  	:returns: Sample POST data and the actual API response.
	"""

	region = check_template_data(request_template_data["regions"], "regions")

	# Return new post data and post response from regions POST request
	randstr = str(randint(0, 1000))
	try:
		name = region["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		region["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing Region property '{e.args[0]}'") from e

	# Check if division already exists, otherwise create it
	region["division"] = division_post_data["id"]
	region["divisionName"] = division_post_data["name"]

	logger.info("New region data to hit POST method %s", request_template_data)
	# Hitting region POST method
	response: tuple[JSONData, requests.Response] = to_session.create_region(data=region)
	resp_obj = check_template_data(response, "regions")
	return resp_obj


@pytest.fixture(name="phys_locations_post_data")
def phys_locations_data_post(to_session: TOSession, request_template_data: list[JSONData],
			  region_post_data: dict[str, object]) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for phys_location endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get phys_location data from a prerequisites file.
	:param region_post_data:
  	:returns: Sample POST data and the actual API response.
	"""

	phys_locations = check_template_data(request_template_data["phys_locations"], "phys_locations")

	# Return new post data and post response from phys_locations POST request
	randstr = str(randint(0, 1000))
	try:
		name = phys_locations["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		phys_locations["name"] = name[:4] + randstr
		short_name = phys_locations["shortName"]
		if not isinstance(name, str):
			raise TypeError(f"shortName must be str, not '{type(short_name)}'")
		phys_locations["shortName"] = short_name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing Phys_location property '{e.args[0]}'") from e

	# Check if region already exists, otherwise create it
	region_id = region_post_data["id"]
	if not isinstance(region_id, int):
		raise TypeError("malformed API response; 'id' property not a integer")
	phys_locations["regionId"] = region_id

	logger.info("New Phys_locations data to hit POST method %s", request_template_data)
	# Hitting region POST method
	response: tuple[JSONData, requests.Response] = to_session.create_physical_locations(
		data=phys_locations)
	resp_obj = check_template_data(response, "phys_locations")
	return resp_obj


@pytest.fixture()
def server_post_data(to_session: TOSession, request_template_data: list[JSONData],
		cdn_post_data: dict[str, object],
		phys_locations_post_data: dict[str, object])-> dict[str, object]:
	"""
	PyTest Fixture to create POST data for server endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get profile data from a prerequisites file.
	:param phys_locations_post_data:
	:returns: Sample POST data and the actual API response.
	"""
	server = check_template_data(request_template_data["servers"], "servers")
	randstr = str(randint(0, 1000))

	# Check if type already exists, otherwise create it
	type_data = check_template_data(request_template_data["types"], "types")
	type_object = create_or_get_existing(to_session, "types", "type", type_data,
				      {"useInTable": "server"})
	type_id = type_object["id"]
	server["typeId"] = type_id

	# Check if cachegroup with type already exists, otherwise create it
	cache_group_data = check_template_data(request_template_data["cachegroup"], "cachegroup")
	cache_group_object = create_or_get_existing(to_session, "cachegroups", "cachegroups",
					    cache_group_data, {"typeId": type_id})
	server["cachegroupId"]= cache_group_object["id"]

	# Check if cdn already exists, otherwise create it
	server["cdnId"] = cdn_post_data["id"]
	server["domainName"] = cdn_post_data["domainName"]

	# Check if profile with cdn already exists, otherwise create it
	profile_data = check_template_data(request_template_data["profiles"], "profiles")
	profile_data["cdn"] = cdn_post_data["id"]
	name = profile_data["name"]
	profile_data["name"] = name[:4] + randstr
	profile_object = create_if_not_exists(to_session, "profile", profile_data)
	server["profileNames"] = [profile_object["name"]]

	# Check if status already exists, otherwise create it
	status_data = check_template_data(request_template_data["status"], "status")
	status_object = create_or_get_existing(to_session, "statuses", "statuses",
					status_data, {"name": "REPORTED"})
	server["statusId"] = status_object["id"]

	# Check if physical location with region already exists, otherwise create it
	physical_location_id = phys_locations_post_data["id"]
	if not isinstance(physical_location_id, int):
		raise TypeError("malformed API response; 'id' property not a integer")
	server["physLocationId"] = physical_location_id

	logger.info("New server data to hit POST method %s", server)
	# Hitting server POST method
	response: tuple[JSONData, requests.Response] = to_session.create_server(data=server)
	resp_obj = check_template_data(response, "server")
	return [resp_obj, profile_object["id"]]


@pytest.fixture(name="delivery_services_post_data")
def delivery_services_data_post(to_session: TOSession, request_template_data: list[JSONData],
				profile_post_data: dict[str, object]
		      ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for server endpoint.
	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get profile data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	delivery_services = check_template_data(
		request_template_data["delivery_services"], "delivery_services")

	randstr = str(randint(0, 1000))
	try:
		xml_id = delivery_services["xmlId"]
		if not isinstance(xml_id, str):
			raise TypeError(f"xmlId must be str, not '{type(xml_id)}'")
		delivery_services["xmlId"] = xml_id[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing delivery_services property '{e.args[0]}'") from e

	# Check if profile with cdn already exists, otherwise create it
	delivery_services["profileId"] = profile_post_data["id"]

	# Check if cdn already exists, otherwise create it
	delivery_services["cdnId"] = profile_post_data["cdn"]

	# Check if tenant already exists, otherwise create it
	tenant_data = check_template_data(request_template_data["tenants"], "tenants")
	tenant_object = create_or_get_existing(to_session, "tenants", "tenant",
					tenant_data, {"name": "root"})
	delivery_services["tenantId"] = tenant_object["id"]

	# Check if type already exists, otherwise create it
	type_data = {"name": "HTTP", "useInTable":"deliveryservice"}
	type_object = create_or_get_existing(to_session, "types", "type", type_data,
				      {"name": "HTTP", "useInTable":"deliveryservice"})
	delivery_services["typeId"] = type_object["id"]
	delivery_services["type"] = type_object["name"]


	logger.info("New delivery_services data to hit POST method %s", delivery_services)
	# Hitting delivery_services POST method
	response: tuple[JSONData, requests.Response] = to_session.create_deliveryservice(
		data=delivery_services)
	resp_obj = check_template_data(response[0], "delivery_services")
	return resp_obj


@pytest.fixture()
def origin_post_data(to_session: TOSession, request_template_data: list[JSONData],
		     delivery_services_post_data: dict[str, object], tenant_post_data: dict[str, object]
		      ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for origins endpoint.
	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get profile data from a prerequisites file.
	:returns: Sample POST data and the actual API response.
	"""
	origin = check_template_data(request_template_data["origins"], "origins")

	randstr = str(randint(0, 1000))
	try:
		name = origin["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		origin["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing origin property '{e.args[0]}'") from e

	# Check if delivery_service already exists, otherwise create it
	delivery_services_id = delivery_services_post_data["id"]
	if not isinstance(delivery_services_id, int):
		raise TypeError("malformed API response; 'id' property not a integer")
	origin["deliveryServiceId"] = delivery_services_id

	# Check if tenant already exists, otherwise create it
	tenant_id = tenant_post_data["id"]
	if not isinstance(tenant_id, int):
		raise TypeError("malformed API response; 'id' property not a integer")
	origin["tenantId"] = tenant_id

	logger.info("New origin data to hit POST method %s", origin)
	# Hitting origins POST method
	response: tuple[JSONData, requests.Response] = to_session.create_origins(data=origin)
	resp_obj = check_template_data(response, "origins")
	return resp_obj


@pytest.fixture()
def status_post_data(to_session: TOSession, request_template_data: list[JSONData]
		  ) -> dict[str, object]:
	"""
	PyTest Fixture to create POST data for statuses endpoint.

	:param to_session: Fixture to get Traffic Ops session.
	:param request_template_data: Fixture to get Status request template data from a prerequisite file.
	:returns: Sample POST data and the actual API response.
	"""
	status = check_template_data(request_template_data["status"], "status")

	# Return new post data and post response from statuses POST request
	randstr = str(randint(0, 1000))
	try:
		name = status["name"]
		if not isinstance(name, str):
			raise TypeError(f"name must be str, not '{type(name)}'")
		status["name"] = name[:4] + randstr
	except KeyError as e:
		raise TypeError(f"missing Status property '{e.args[0]}'") from e

	logger.info("New status data to hit POST method %s", status)
	# Hitting statuses POST methed
	response: tuple[JSONData, requests.Response] = to_session.create_statuses(data=status)
	resp_obj = check_template_data(response, "statuses")
	return resp_obj
