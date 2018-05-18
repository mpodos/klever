#!/usr/bin/env python3
#
# Copyright (c) 2017 ISPRAS (http://www.ispras.ru)
# Institute for System Programming of the Russian Academy of Sciences
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

import glob
import os
# import subprocess


def execute_cmd(*args, stdin=None, get_output=False, username=None):
    if username:
        args = ['su', '-m', username, '-c'] + args

    print('Execute command "{0}"'.format(' '.join(args)))
    # if get_output:
    #     return subprocess.check_output(args, stdin=stdin).decode('utf8')
    # else:
    #     subprocess.check_call(args, stdin=stdin)


def prepare_env(username, deploy_dir, psql_user_passwd, psql_user_name='klever', create_user=False):
    if create_user:
        print('Create user')
        execute_cmd('useradd', 'klever')

    print('Prepare working directory')
    work_dir = os.path.join(deploy_dir, 'work')
    execute_cmd('mkdir', work_dir)
    execute_cmd('chown', '-LR', '{0}:{0}'.format(username), work_dir)

    print('Create soft links for libssl to build new versions of the Linux kernel')
    execute_cmd('ln', '-s', '/usr/include/x86_64-linux-gnu/openssl/opensslconf.h', '/usr/include/openssl/')

    print('Prepare CIF environment')
    args = glob.glob('/usr/lib/x86_64-linux-gnu/crt*.o')
    args.append('/usr/lib')
    execute_cmd('ln', '-s', *args)

    print('Create PostgreSQL user')
    execute_cmd('psql', '-c', "CREATE USER {0} WITH PASSWORD '{1}'".format(psql_user_name, psql_user_passwd),
                username='postgres')

    print('Create PostgreSQL database')
    execute_cmd('createdb', '-T', 'template0', '-E', 'utf8', 'klever' , username='postgres')

    print('Prepare Klever Bridge media directory')
    media_dir = os.path.join(deploy_dir, 'media')
    execute_cmd('mkdir', media_dir)
    execute_cmd('chown', '-R', 'www-data:www-data', media_dir)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--klever-username')
    parser.add_argument('--klever-user-password', default=None)
    parser.add_argument('--klever-working-directory')
    parser.add_argument('--previous-build-configuration-file', default=None)
    parser.add_argument('--new-build-configuration-file')
    args = parser.parse_args()

    prepare_env(args.klever_configuration_file, args.previous_build_configuration_file,
                args.new_build_configuration_file, args.non_interactive)