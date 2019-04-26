import os
import json
import uuid

from django.conf import settings
from django.utils.functional import cached_property

from bridge.utils import logger, BridgeException, file_get_or_create, RMQConnect
from bridge.vars import JOB_ROLES, JOB_STATUS

from jobs.models import JobFile, Job
from jobs.serializers import CreateJobSerializer

JOB_SETTINGS_FILE = 'settings.json'


class JobsPopulation:
    jobs_dir = os.path.join(settings.BASE_DIR, 'jobs', 'presets')

    def __init__(self, user=None):
        self._user = user

    @cached_property
    def common_files(self):
        # Directory "specifications" and files "program fragmentation.json" and "verifier profiles.json"
        # should be added for all preset jobs.
        return [
            self.__get_dir(os.path.join(self.jobs_dir, 'specifications'), 'specifications'),
            self.__get_file(os.path.join(self.jobs_dir, 'program fragmentation.json'), 'program fragmentation.json'),
            self.__get_file(os.path.join(self.jobs_dir, 'verifier profiles.json'), 'verifier profiles.json')
        ]

    def populate(self):
        created_jobs = []
        for dirpath, dirnames, filenames in os.walk(self.jobs_dir):
            # Do not traverse within specific directories. Directory "specifications" should be placed within the root
            # preset jobs directory, directory "staging" can be placed anywhere.
            if os.path.basename(dirpath) == 'specifications' or os.path.basename(dirpath) == 'staging':
                dirnames[:] = []
                filenames[:] = []
                continue

            # Directories without preset job settings file serve to keep ones with that file and specific ones.
            job_settings_file = os.path.join(dirpath, JOB_SETTINGS_FILE)
            if not os.path.exists(job_settings_file):
                continue

            # Do not traverse within directories with preset job settings file.
            dirnames[:] = []

            data = self.__get_settings_data(job_settings_file)
            data.update({
                'global_role': JOB_ROLES[1][0], 'parent': None,
                'files': [{
                    'type': 'root', 'text': 'Root',
                    'children': self.common_files + self.__get_children(dirpath)
                }]
            })
            serializer = CreateJobSerializer(data=data, context={'author': self._user})
            if not serializer.is_valid(raise_exception=True):
                logger.error(serializer.errors)
                raise BridgeException('Job data validation failed')
            job = serializer.save()

            created_jobs.append([job.name, str(job.identifier)])
        return created_jobs

    def __get_settings_data(self, filepath):
        data = {}

        # Parse settings
        with open(filepath, encoding='utf8') as fp:
            try:
                job_settings = json.load(fp)
            except Exception as e:
                logger.exception(e)
                raise BridgeException('Settings file is not valid JSON file')

        # Get unique name
        name = job_settings.get('name')
        if not isinstance(name, str) or len(name) == 0:
            raise BridgeException('Preset job name is required')
        job_name = name
        cnt = 1
        while True:
            try:
                Job.objects.get(name=job_name)
            except Job.DoesNotExist:
                break
            cnt += 1
            job_name = "%s #%s" % (name, cnt)
        data['name'] = job_name

        # Get description if specified
        description = job_settings.get('description')
        if isinstance(description, str):
            data['description'] = description

        # Get identifier if it is specified
        if 'identifier' in job_settings:
            try:
                data['identifier'] = uuid.UUID(job_settings['identifier'])
            except Exception as e:
                logger.exception(e)
                raise BridgeException('Job identifier has wrong format, uuid expected')

        return data

    def __get_file(self, path, fname):
        with open(path, mode='rb') as fp:
            db_file = file_get_or_create(fp, fname, JobFile, True)
        return {'type': 'file', 'text': fname, 'data': {'hashsum': db_file.hash_sum}}

    def __get_dir(self, path, fname):
        return {'type': 'folder', 'text': fname, 'children': self.__get_children(path)}

    def __get_children(self, root):
        children = []
        for fname in os.listdir(root):
            if fname == JOB_SETTINGS_FILE:
                continue
            path = os.path.join(root, fname)
            if os.path.isfile(path):
                children.append(self.__get_file(path, fname))
            elif os.path.isdir(path):
                children.append(self.__get_dir(path, fname))
        return children


class JobsRMQPopulation:
    JOB_RMQ_QUEUES = {
        JOB_STATUS[1][0]: 'jobs_pending',
        JOB_STATUS[2][0]: 'jobs_solving',
        JOB_STATUS[3][0]: 'jobs_solved',
        JOB_STATUS[4][0]: 'jobs_failed',
        JOB_STATUS[5][0]: 'jobs_corrupted',
        JOB_STATUS[6][0]: 'jobs_cancelling',
        JOB_STATUS[7][0]: 'jobs_cancelled',
        JOB_STATUS[8][0]: 'jobs_terminated'
    }

    def __init__(self):
        self._exchange = settings.RABBIT_MQ['jobs_exchange']
        self.__populate()

    def __populate(self):
        with RMQConnect() as channel:
            channel.exchange_declare(exchange=self._exchange, exchange_type='direct')
            for job_status, queue_name in self.JOB_RMQ_QUEUES.items():
                channel.queue_declare(queue=queue_name, durable=True)
                channel.queue_bind(exchange=self._exchange, queue=queue_name, routing_key=job_status)