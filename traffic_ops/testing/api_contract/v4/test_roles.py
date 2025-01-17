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

"""API Contract Test Case for roles endpoint."""
import logging
from typing import Union

import pytest
import requests
from jsonschema import validate

from trafficops.tosession import TOSession

# Create and configure logger
logger = logging.getLogger()

Primitive = Union[bool, int, float, str, None]


def test_role_contract(to_session: TOSession,
	response_template_data: dict[str, Union[Primitive, list[Union[Primitive,
							dict[str, object], list[object]]],
	dict[object, object]]], role_post_data: dict[str, object]) -> None:
	"""
	Test step to validate keys, values and data types from roles endpoint
	response.
	:param to_session: Fixture to get Traffic Ops session.
	:param response_template_data: Fixture to get response template data from a prerequisites file.
	:param role_post_data: Fixture to get sample role data and actual role response.
	"""
	# validate Role keys from roles get response
	logger.info("Accessing /roles endpoint through Traffic ops session.")

	role_name = role_post_data.get("name")
	if not isinstance(role_name, str):
		raise TypeError("malformed role in prerequisite data; 'name' not a string")

	role_get_response: tuple[
		Union[dict[str, object], list[Union[dict[str, object], list[object], Primitive]], Primitive],
		requests.Response
	] = to_session.get_roles(query_params={"name": role_name})
	try:
		role_data = role_get_response[0]
		if not isinstance(role_data, list):
			raise TypeError("malformed API response; 'response' property not an array")

		first_role = role_data[0]
		if not isinstance(first_role, dict):
			raise TypeError("malformed API response; first role in response is not an dict")
		logger.info("Role Api get response %s", first_role)

		role_response_template = response_template_data.get("roles")
		if not isinstance(role_response_template, dict):
			raise TypeError(
				f"Role response template data must be a dict, not '{type(role_response_template)}'")

		# validate roles values from prereq data in roles get response.
		prereq_values = [role_post_data["name"], role_post_data["description"]]
		get_values = [first_role["name"], first_role["description"]]

		# validate keys,data types for values and values from roles get json response.
		assert validate(instance=first_role, schema=role_response_template) is None
		assert get_values == prereq_values
	except IndexError:
		logger.error("Either prerequisite data or API response was malformed")
		pytest.fail("API contract test failed for roles endpoint: API response was malformed")
	finally:
		# Delete Role after test execution to avoid redundancy.
		role_name = role_post_data.get("name")
		if to_session.delete_role(query_params={"name": role_name}) is None:
			logger.error("Role returned by Traffic Ops is missing an 'name' property")
			pytest.fail("Response from delete request is empty, Failing test_role_contract")
