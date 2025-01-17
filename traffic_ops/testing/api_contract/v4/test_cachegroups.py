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

"""API Contract Test Case for cachegroup endpoint."""
import logging
from typing import Union

import pytest
import requests
from jsonschema import validate

from trafficops.tosession import TOSession

# Create and configure logger
logger = logging.getLogger()

Primitive = Union[bool, int, float, str, None]


def test_cache_group_contract(to_session: TOSession,
							  response_template_data: dict[str, Union[Primitive, list[
								  Union[Primitive, dict[str, object], list[object]]], dict[
								  object, object]]],
							  cache_group_post_data: dict[str, object]
							  ) -> None:
	"""
	Test step to validate keys, values and data types from cachegroup endpoint
	response.
	:param to_session: Fixture to get Traffic Ops session.
	:param response_template_data: Fixture to get response template data from a prerequisites file.
	:param cache_group_post_data: Fixture to get sample cachegroup data and actual cachegroup response.
	"""
	# validate CDN keys from cdns get response
	logger.info("Accessing /cachegroup endpoint through Traffic ops session.")

	cache_group_name = cache_group_post_data.get("name")
	if not isinstance(cache_group_name, str):
		raise TypeError("malformed cachegroup in prerequisite data; 'name' not a string")

	cache_group_get_response: tuple[
		Union[dict[str, object], list[Union[dict[str, object], list[object], Primitive]], Primitive],
		requests.Response
	] = to_session.get_cachegroups(query_params={"name": str(cache_group_name)})

	try:
		cache_group_data = cache_group_get_response[0]
		if not isinstance(cache_group_data, list):
			raise TypeError("malformed API response; 'response' property not an array")

		first_cache_group = cache_group_data[0]
		if not isinstance(first_cache_group, dict):
			raise TypeError("malformed API response; first Cache group in response is not an dict")
		logger.info("Cachegroup API get response %s", first_cache_group)
		cache_group_response_template = response_template_data.get("cachegroup")

		# validate cachegroup values from prereq data in cachegroup get response.
		keys = ["name", "shortName", "fallbackToClosest", "typeId"]
		prereq_values = [cache_group_post_data[key] for key in keys]
		get_values = [first_cache_group[key] for key in keys]

		# validate keys,data types and values for cachegroup endpoint.
		assert validate(instance=first_cache_group, schema=cache_group_response_template) is None
		assert get_values == prereq_values
	except IndexError:
		logger.error("Either prerequisite data or API response was malformed")
		pytest.fail("API contract test failed for cachegroup endpoint: API response was malformed")
	finally:
		# Delete Cache group after test execution to avoid redundancy.
		cache_group_id = cache_group_post_data.get("id")
		if to_session.delete_cachegroups(cache_group_id=cache_group_id) is None:
			logger.error("Cachegroup returned by Traffic Ops is missing an 'id' property")
			pytest.fail("Response from delete request is empty, Failing test_cachegroup_contract")
