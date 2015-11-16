#!/usr/bin/env python3
import json
import argparse
import logging
import os

import Cloud.utils as utils
import Cloud.client as client


if __name__ == "__main__":
    # Parse configuration
    parser = argparse.ArgumentParser(description='Start cloud Klever scheduler client according to the provided '
                                                 'configuration.')
    parser.add_argument('mode', metavar="MODE", help='TASK or JOB.')
    parser.add_argument('conf', metavar="CONF", help='JSON string with all necessary configuration.')
    parser.add_argument('--file', metavar="FILE", help='File with configuration. Use it during debug.')
    args = parser.parse_args()

    if args.mode == "JOB":
        if args.file and os.path.isfile(args.file):
            with open(args.file) as fh:
                conf = json.loads(fh.read())
        else:
            conf = json.loads(str(args.conf))

        conf = utils.common_initialization("Client controller", conf)
        logging.info("Proceed to job solution")
        exit_code = client.solve_job(conf)
        logging.info("Exiting with psi exit code {}".format(exit_code))
        exit(int(exit_code))
    elif args.mode == "TASK":
        exit(0)
    else:
        NotImplementedError("Provided mode {} is not supported by the client".format(args.mode))

__author__ = 'Ilja Zakharov <ilja.zakharov@ispras.ru>'
