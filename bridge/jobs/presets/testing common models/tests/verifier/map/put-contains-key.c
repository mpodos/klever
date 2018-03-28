/*
 * Copyright (c) 2017 ISPRAS (http://www.ispras.ru)
 * Institute for System Programming of the Russian Academy of Sciences
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * ee the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <linux/module.h>
#include <ldv-test.h>
#include <verifier/map.h>

static int __init ldv_init(void)
{
	ldv_map map;
	ldv_map_value value = 1;

	ldv_map_init(map);

	ldv_map_put(map, key, value);
	if (ldv_map_contains_key(map, key))
		ldv_error();

	return 0;
}

module_init(ldv_init);