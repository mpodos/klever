#
# Copyright (c) 2014-2016 ISPRAS (http://www.ispras.ru)
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

import os
import re
import json
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.translation import ugettext_lazy as _, override
from django.utils.timezone import datetime, pytz

from bridge.vars import JOB_CLASSES, FORMAT, JOB_STATUS, REPORT_FILES_ARCHIVE, JOB_WEIGHT
from bridge.utils import logger, file_get_or_create, BridgeException
from bridge.ZipGenerator import ZipStream, CHUNK_SIZE

from jobs.models import Job, RunHistory, JobFile
from reports.models import Report, ReportRoot, ReportSafe, ReportUnsafe, ReportUnknown, ReportComponent,\
    Component, Computer, ReportAttr, LightResource
from jobs.utils import create_job, update_job, change_job_status
from reports.utils import AttrData
from tools.utils import Recalculation


class KleverCoreArchiveGen:
    def __init__(self, job):
        self.arcname = 'VJ__' + job.identifier + '.zip'
        self.job = job
        self.stream = ZipStream()

    def __iter__(self):
        for f in self.__get_job_files():
            for data in self.stream.compress_file(f['src'], f['path']):
                yield data

        for data in self.stream.compress_string('format', str(self.job.format)):
            yield data

        for job_class in JOB_CLASSES:
            if job_class[0] == self.job.type:
                with override('en'):
                    for data in self.stream.compress_string('class', str(job_class[1])):
                        yield data
                break
        yield self.stream.close_stream()

    def __get_job_files(self):
        job_file_system = {}
        files_to_add = set()
        for f in self.job.versions.get(version=self.job.version).filesystem_set.all():
            job_file_system[f.id] = {
                'parent': f.parent_id,
                'name': f.name,
                'src': os.path.join(settings.MEDIA_ROOT, f.file.file.name) if f.file is not None else None
            }
            if job_file_system[f.id]['src'] is not None:
                files_to_add.add(f.id)
        job_files = []
        for f_id in files_to_add:
            file_path = job_file_system[f_id]['name']
            parent_id = job_file_system[f_id]['parent']
            while parent_id is not None:
                file_path = os.path.join(job_file_system[parent_id]['name'], file_path)
                parent_id = job_file_system[parent_id]['parent']
            job_files.append({
                'path': os.path.join('root', file_path),
                'src': job_file_system[f_id]['src']
            })
        return job_files


class JobArchiveGenerator:
    def __init__(self, job):
        self.job = job
        self.arcname = 'Job-%s-%s.zip' % (self.job.identifier[:10], self.job.type)
        self.arch_files = {}
        self.files_to_add = []
        self.stream = ZipStream()

    def __iter__(self):
        for job_v in self.job.versions.all():
            for data in self.stream.compress_string('version-%s.json' % job_v.version, self.__version_data(job_v)):
                yield data
        for data in self.stream.compress_string('job.json', self.__job_data()):
            yield data
        reportsdata = ReportsData(self.job)
        for data in self.stream.compress_string('reports.json', json.dumps(
                reportsdata.reports, ensure_ascii=False, sort_keys=True, indent=4).encode('utf-8')):
            yield data
        for data in self.stream.compress_string('computers.json', json.dumps(
                reportsdata.computers, ensure_ascii=False, sort_keys=True, indent=4).encode('utf-8')):
            yield data
        for data in self.stream.compress_string('LightWeightCache.json', json.dumps(
                LightWeightCache(self.job).data, ensure_ascii=False, sort_keys=True, indent=4).encode('utf-8')):
            yield data
        self.__add_reports_files()
        for file_path, arcname in self.files_to_add:
            for data in self.stream.compress_file(file_path, arcname):
                yield data
        yield self.stream.close_stream()

    def __version_data(self, job_v):
        filedata = []
        for f in job_v.filesystem_set.all():
            filedata_element = {
                'pk': f.pk, 'parent': f.parent_id, 'name': f.name, 'file': f.file_id
            }
            if f.file is not None:
                if f.file.pk not in self.arch_files:
                    self.arch_files[f.file.pk] = f.file.file.name
                    self.files_to_add.append((os.path.join(settings.MEDIA_ROOT, f.file.file.name), f.file.file.name))
            filedata.append(filedata_element)
        return json.dumps({
            'filedata': filedata,
            'description': job_v.description,
            'name': job_v.name,
            'global_role': job_v.global_role,
            'comment': job_v.comment,
        }, ensure_ascii=False, sort_keys=True, indent=4).encode('utf-8')

    def __job_data(self):
        return json.dumps({
            'format': self.job.format, 'identifier': self.job.identifier, 'type': self.job.type,
            'status': self.job.status, 'files_map': self.arch_files, 'run_history': self.__add_run_history_files(),
            'weight': self.job.weight, 'safe_marks': self.job.safe_marks
        }, ensure_ascii=False, sort_keys=True, indent=4).encode('utf-8')

    def __add_run_history_files(self):
        data = []
        for rh in self.job.runhistory_set.order_by('date'):
            self.files_to_add.append((
                os.path.join(settings.MEDIA_ROOT, rh.configuration.file.name),
                os.path.join('Configurations', "%s.json" % rh.pk)
            ))
            data.append({'id': rh.pk, 'status': rh.status, 'date': rh.date.timestamp()})
        return data

    def __add_reports_files(self):
        tables = [ReportSafe, ReportUnsafe, ReportUnknown, ReportComponent]
        for table in tables:
            for report in table.objects.filter(root__job=self.job):
                if report.archive:
                    self.files_to_add.append((
                        os.path.join(settings.MEDIA_ROOT, report.archive.name),
                        os.path.join(table.__name__, '%s.zip' % report.pk)
                    ))


class JobsArchivesGen:
    def __init__(self, jobs):
        self.jobs = jobs
        self.stream = ZipStream()

    def __iter__(self):
        for job in self.jobs:
            jobgen = JobArchiveGenerator(job)
            buf = b''
            for data in self.stream.compress_stream(jobgen.arcname, jobgen):
                buf += data
                if len(buf) > CHUNK_SIZE:
                    yield buf
                    buf = b''
            if len(buf) > 0:
                yield buf
        yield self.stream.close_stream()


class LightWeightCache:
    def __init__(self, job):
        self.data = {}
        try:
            self.root = ReportRoot.objects.get(job=job)
        except ObjectDoesNotExist:
            return
        if job.weight == JOB_WEIGHT[1][0]:
            self.data['resources'] = self.__get_light_resources()

    def __get_light_resources(self):
        res_data = []
        for r in LightResource.objects.filter(report=self.root):
            res_data.append({
                'component': r.component.name if r.component is not None else None,
                'wall_time': r.wall_time, 'cpu_time': r.cpu_time, 'memory': r.memory
            })
        return res_data


class ReportsData(object):
    def __init__(self, job):
        self.computers = {}
        self._parents = {None: None}
        try:
            self.root = ReportRoot.objects.get(job=job)
        except ObjectDoesNotExist:
            self.reports = []
        else:
            self.reports = self.__reports_data()

    def __report_component_data(self, report):
        data = None
        if report.data:
            with report.data as fp:
                data = fp.read().decode('utf8')
        if report.computer_id not in self.computers:
            self.computers[str(report.computer_id)] = report.computer.description
        self._parents[report.id] = report.identifier
        return {
            'pk': report.pk,
            'parent': self._parents[report.parent_id],
            'identifier': report.identifier,
            'computer': str(report.computer_id),
            'component': report.component.name,
            'verification': report.verification,
            'resource': {
                'cpu_time': report.cpu_time,
                'wall_time': report.wall_time,
                'memory': report.memory,
            } if all(x is not None for x in [report.cpu_time, report.wall_time, report.memory]) else None,
            'start_date': report.start_date.timestamp(),
            'finish_date': report.finish_date.timestamp() if report.finish_date is not None else None,
            'data': data,
            'attrs': [],
            'log': report.log
        }

    def __report_leaf_data(self, report):
        data = {
            'pk': report.pk,
            'parent': self._parents[report.parent_id],
            'identifier': report.identifier,
            'attrs': []
        }
        if isinstance(report, ReportSafe):
            data['proof'] = report.proof
            data['verifier_time'] = report.verifier_time
        elif isinstance(report, ReportUnsafe):
            data['error_trace'] = report.error_trace
            data['verifier_time'] = report.verifier_time
        elif isinstance(report, ReportUnknown):
            data['problem_description'] = report.problem_description
            data['component'] = report.component.name
        return data

    def __reports_data(self):
        reports = []
        report_index = {}
        i = 0
        for rc in ReportComponent.objects.filter(root=self.root).select_related('component').order_by('id'):
            report_index[rc.pk] = i
            reports.append(self.__report_component_data(rc))
            i += 1
        reports.append(ReportSafe.__name__)
        i += 1
        for safe in ReportSafe.objects.filter(root=self.root):
            report_index[safe.pk] = i
            reports.append(self.__report_leaf_data(safe))
            i += 1
        reports.append(ReportUnsafe.__name__)
        i += 1
        for unsafe in ReportUnsafe.objects.filter(root=self.root):
            report_index[unsafe.pk] = i
            reports.append(self.__report_leaf_data(unsafe))
            i += 1
        reports.append(ReportUnknown.__name__)
        i += 1
        for unknown in ReportUnknown.objects.filter(root=self.root).select_related('component'):
            report_index[unknown.pk] = i
            reports.append(self.__report_leaf_data(unknown))
            i += 1
        for ra in ReportAttr.objects.filter(report__root=self.root).select_related('attr', 'attr__name').order_by('id'):
            reports[report_index[ra.report_id]]['attrs'].append((ra.attr.name.name, ra.attr.value))
        return reports

    def __is_not_used(self):
        pass


class UploadJob(object):
    def __init__(self, parent, user, job_dir):
        self.parent = parent
        self.job = None
        self.user = user
        self.job_dir = job_dir
        self.__create_job_from_tar()

    def __create_job_from_tar(self):
        jobdata = None
        reports_data = None
        computers = {}
        files_in_db = {}
        versions_data = {}
        report_files = {}
        run_history_files = {}
        light_cache = {}
        for dir_path, dir_names, file_names in os.walk(self.job_dir):
            for file_name in file_names:
                rel_path = os.path.relpath(os.path.join(dir_path, file_name), self.job_dir)
                if rel_path == 'job.json':
                    with open(os.path.join(dir_path, file_name), encoding='utf8') as fp:
                        jobdata = json.load(fp)
                elif rel_path == 'LightWeightCache.json':
                    with open(os.path.join(dir_path, file_name), encoding='utf8') as fp:
                        light_cache = json.load(fp)
                elif rel_path == 'reports.json':
                    with open(os.path.join(dir_path, file_name), encoding='utf8') as fp:
                        reports_data = json.load(fp)
                elif rel_path == 'computers.json':
                    with open(os.path.join(dir_path, file_name), encoding='utf8') as fp:
                        computers = json.load(fp)
                elif rel_path.startswith('version-'):
                    m = re.match('version-(\d+)\.json', rel_path)
                    if m is None:
                        raise BridgeException(_('Unknown file in the archive: %(filename)s') % {'filename': rel_path})
                    with open(os.path.join(dir_path, file_name), encoding='utf8') as fp:
                        versions_data[int(m.group(1))] = json.load(fp)
                elif rel_path.startswith('Configurations'):
                    run_history_files[int(file_name.replace('.json', ''))] = os.path.join(dir_path, file_name)
                else:
                    b_dir = os.path.basename(dir_path)
                    if not rel_path.startswith(b_dir):
                        raise BridgeException(_('Unknown file in the archive: %(filename)s') % {'filename': rel_path})
                    if b_dir in {'ReportSafe', 'ReportUnsafe', 'ReportUnknown', 'ReportComponent'}:
                        m = re.match('(\d+)\.zip', file_name)
                        if m is not None:
                            report_files[(b_dir, int(m.group(1)))] = os.path.join(dir_path, file_name)
                    else:
                        try:
                            files_in_db[b_dir + '/' + file_name] = file_get_or_create(
                                open(os.path.join(dir_path, file_name), mode='rb'), file_name, JobFile, True
                            )[1]
                        except Exception as e:
                            logger.exception("Can't save job files to DB: %s" % e)
                            raise BridgeException(_("Creating job's file failed"))

        if not isinstance(jobdata, dict):
            raise ValueError('job.json file was not found or contains wrong data')
        # Check job data
        if any(x not in jobdata for x in ['format', 'type', 'status', 'files_map',
                                          'run_history', 'weight', 'safe_marks']):
            raise ValueError('Not enough data in job.json file')
        if jobdata['format'] != FORMAT:
            raise BridgeException(_("The job format is not supported"))
        if 'identifier' in jobdata:
            if isinstance(jobdata['identifier'], str) and len(jobdata['identifier']) > 0:
                if len(Job.objects.filter(identifier=jobdata['identifier'])) > 0:
                    raise BridgeException(_("The job with identifier specified in the archive already exists"))
            else:
                del jobdata['identifier']
        if jobdata['type'] != self.parent.type:
            raise BridgeException(_("The job class does not equal to the parent class"))
        if jobdata['weight'] not in set(w[0] for w in JOB_WEIGHT):
            raise ValueError('Wrong job weight: %s' % jobdata['weight'])
        if jobdata['status'] not in list(x[0] for x in JOB_STATUS):
            raise ValueError("The job status is wrong: %s" % jobdata['status'])
        for f_id in list(jobdata['files_map']):
            if jobdata['files_map'][f_id] in files_in_db:
                jobdata['files_map'][int(f_id)] = files_in_db[jobdata['files_map'][f_id]]
                del jobdata['files_map'][f_id]
            else:
                raise ValueError('Not enough files in "Files" directory')

        # Check versions data
        if len(versions_data) == 0:
            raise ValueError("There are no job's versions in the archive")
        for version in versions_data:
            if any(x not in versions_data[version] for x in
                   ['name', 'description', 'comment', 'global_role', 'filedata']):
                raise ValueError("The job version data is corrupted")

        # Update versions' files data
        version_list = list(versions_data[v] for v in sorted(versions_data))
        for i in range(0, len(version_list)):
            version_filedata = []
            for file in version_list[i]['filedata']:
                fdata_elem = {
                    'title': file['name'],
                    'id': file['pk'],
                    'type': '0',
                    'parent': file['parent'],
                    'hash_sum': None
                }
                if file['file'] is not None:
                    fdata_elem['type'] = '1'
                    fdata_elem['hash_sum'] = jobdata['files_map'][file['file']]
                version_filedata.append(fdata_elem)
            version_list[i]['filedata'] = version_filedata

        # Creating the job
        try:
            job = create_job({
                'name': version_list[0]['name'],
                'identifier': jobdata.get('identifier'),
                'author': self.user,
                'description': version_list[0]['description'],
                'parent': self.parent,
                'type': self.parent.type,
                'global_role': version_list[0]['global_role'],
                'filedata': version_list[0]['filedata'],
                'comment': version_list[0]['comment'],
                'safe_marks': jobdata['safe_marks']
            })
        except Exception as e:
            logger.exception(e, stack_info=True)
            raise BridgeException(_('Saving the job failed'))
        job.weight = jobdata['weight']
        job.save()

        # Creating job's run history
        try:
            for rh in jobdata['run_history']:
                if rh['status'] not in list(x[0] for x in JOB_STATUS):
                    raise BridgeException(_("The job archive is corrupted"))
                with open(run_history_files[rh['id']], mode='rb') as fp:
                    RunHistory.objects.create(
                        job=job, status=rh['status'],
                        date=datetime.fromtimestamp(rh['date'], pytz.timezone('UTC')),
                        configuration=file_get_or_create(fp, 'config.json', JobFile)[0]
                    )
        except Exception as e:
            job.delete()
            raise ValueError("Run history data is corrupted: %s" % e)

        # Creating job's versions
        for version_data in version_list[1:]:
            try:
                update_job({
                    'job': job,
                    'name': version_data['name'],
                    'author': self.user,
                    'description': version_data['description'],
                    'parent': self.parent,
                    'type': self.parent.type,
                    'filedata': version_data['filedata'],
                    'global_role': version_data['global_role'],
                    'comment': version_data['comment']
                })
            except Exception as e:
                logger.exception(e)
                job.delete()
                raise BridgeException(_('Updating the job failed'))

        # Change job's status as it was in downloaded archive
        change_job_status(job, jobdata['status'])
        ReportRoot.objects.create(user=self.user, job=job)
        try:
            UploadReports(job, computers, reports_data, report_files)
        except BridgeException:
            job.delete()
            raise
        except Exception as e:
            logger.exception("Uploading reports failed: %s" % e, stack_info=True)
            job.delete()
            raise BridgeException(_("Unknown error while uploading reports"))
        self.job = job
        self.__fill_lightweight_cache(light_cache)

    def __fill_lightweight_cache(self, light_cache):
        try:
            root = ReportRoot.objects.get(job=self.job)
        except ObjectDoesNotExist:
            return
        if 'resources' in light_cache:
            LightResource.objects.bulk_create(list(LightResource(
                report=root, wall_time=int(d['wall_time']), cpu_time=int(d['cpu_time']), memory=int(d['memory']),
                component=Component.objects.get_or_create(name=d['component'])[0]
                if d['component'] is not None else None
            ) for d in light_cache['resources']))


class UploadReports:
    def __init__(self, job, computers, data, files):
        self.job = job
        self.data = data
        self.files = files
        self._parents = {None: None}
        self._indexes = {}
        self._tree = []
        self._unsafes = []
        self._safes = []
        self._unknowns = []
        self._computers = computers
        self.__upload_computers()
        self._components = {}
        self._attrs = AttrData()
        self.__upload_all()

    def __fix_identifer(self, i):
        m = re.match('.*?(/.*)', self.data[i]['identifier'])
        if m is None:
            self.data[i]['identifier'] = self.job.identifier
        else:
            self.data[i]['identifier'] = self.job.identifier + m.group(1)

    def __upload_computers(self):
        for c_id in self._computers:
            computer = Computer.objects.get_or_create(description=self._computers[c_id])[0]
            self._computers[c_id] = computer.id

    def __upload_all(self):
        curr_func = self.__add_report_component
        for i in range(len(self.data)):
            if isinstance(self.data[i], dict):
                self.__fix_identifer(i)
                curr_func(i)
            elif isinstance(self.data[i], str) and self.data[i] == ReportSafe.__name__:
                def curr_func(x):
                    self._safes.append(x)
                    self._indexes[self.data[x]['identifier']] = x
            elif isinstance(self.data[i], str) and self.data[i] == ReportUnsafe.__name__:
                def curr_func(x):
                    self._unsafes.append(x)
                    self._indexes[self.data[x]['identifier']] = x
            elif isinstance(self.data[i], str) and self.data[i] == ReportUnknown.__name__:
                def curr_func(x):
                    self._unknowns.append(x)
                    self._indexes[self.data[x]['identifier']] = x
        for lvl in range(len(self._tree)):
            self.__upload_report_components(lvl)
            for report in ReportComponent.objects.filter(root=self.job.reportroot):
                self._parents[report.identifier] = report.id
        self.__upload_safe_reports()
        self.__upload_unsafe_reports()
        self.__upload_unknown_reports()
        for report in Report.objects.filter(root=self.job.reportroot).only('id', 'identifier'):
            i = self._indexes[report.identifier]
            for attr in self.data[i]['attrs']:
                self._attrs.add(report.id, attr[0], attr[1])
        self._attrs.upload()
        Recalculation('all', json.dumps([self.job.pk], ensure_ascii=False))

    @transaction.atomic
    def __upload_report_components(self, lvl):
        for identifier in self._tree[lvl]:
            i = self._indexes[identifier]
            report = ReportComponent(
                identifier=identifier, root=self.job.reportroot,
                parent_id=self._parents[self.data[i].get('parent')],
                computer_id=self._computers[self.data[i]['computer']],
                component_id=self.__get_component(self.data[i]['component']),
                verification=self.data[i]['verification'],
                start_date=datetime.fromtimestamp(self.data[i]['start_date'], pytz.timezone('UTC')),
                finish_date=datetime.fromtimestamp(self.data[i]['finish_date'], pytz.timezone('UTC'))
                if self.data[i]['finish_date'] is not None else None,
                log=self.data[i].get('log')
            )
            if self.data[i]['resource'] is not None:
                report.cpu_time = self.data[i]['resource']['cpu_time']
                report.wall_time = self.data[i]['resource']['wall_time']
                report.memory = self.data[i]['resource']['memory']
            if (ReportComponent.__name__, self.data[i]['pk']) in self.files:
                with open(self.files[(ReportComponent.__name__, self.data[i]['pk'])], mode='rb') as fp:
                    report.new_archive(REPORT_FILES_ARCHIVE, fp)
            if self.data[i]['data'] is not None:
                report.new_data('report-data.json', BytesIO(self.data[i]['data'].encode('utf8')))
            report.save()

    @transaction.atomic
    def __upload_safe_reports(self):
        for i in self._safes:
            report = ReportSafe(
                root=self.job.reportroot, identifier=self.data[i]['identifier'],
                parent_id=self._parents[self.data[i]['parent']], verifier_time=self.data[i]['verifier_time']
            )
            if (ReportSafe.__name__, self.data[i]['pk']) in self.files:
                with open(self.files[(ReportSafe.__name__, self.data[i]['pk'])], mode='rb') as fp:
                    report.proof = self.data[i]['proof']
                    report.new_archive(REPORT_FILES_ARCHIVE, fp)
            report.save()

    @transaction.atomic
    def __upload_unsafe_reports(self):
        for i in self._unsafes:
            report = ReportUnsafe(
                root=self.job.reportroot, identifier=self.data[i]['identifier'],
                parent_id=self._parents[self.data[i]['parent']], error_trace=self.data[i]['error_trace'],
                verifier_time=self.data[i]['verifier_time']
            )
            with open(self.files[(ReportUnsafe.__name__, self.data[i]['pk'])], mode='rb') as fp:
                report.new_archive(REPORT_FILES_ARCHIVE, fp)
            report.save()

    @transaction.atomic
    def __upload_unknown_reports(self):
        for i in self._unknowns:
            report = ReportUnknown(
                root=self.job.reportroot, identifier=self.data[i]['identifier'],
                parent_id=self._parents[self.data[i]['parent']],
                problem_description=self.data[i]['problem_description'],
                component_id=self.__get_component(self.data[i]['component'])
            )
            with open(self.files[(ReportUnknown.__name__, self.data[i]['pk'])], mode='rb') as fp:
                report.new_archive(REPORT_FILES_ARCHIVE, fp)
            report.save()

    def __get_component(self, name):
        if name not in self._components:
            component = Component.objects.get_or_create(name=name)[0]
            self._components[name] = component.id
        return self._components[name]

    def __add_report_component(self, i):
        p_id = self.data[i].get('parent')
        if p_id is None:
            self.__add_to_tree(0, i)
        else:
            for j in range(len(self._tree)):
                if p_id in self._tree[j]:
                    self.__add_to_tree(j + 1, i)
                    break
            else:
                raise ValueError('The report parent was not found in data')

    def __add_to_tree(self, lvl, i):
        while len(self._tree) <= lvl:
            self._tree.append(set())
        self._tree[lvl].add(self.data[i]['identifier'])
        self._indexes[self.data[i]['identifier']] = i


def update_identifier(job_id):
    from bridge.utils import unique_id
    job = Job.objects.get(id=job_id)
    new_id = unique_id()
    len_old = len(job.identifier)
    job.identifier = new_id
    job.save()
    with transaction.atomic():
        for r in Report.objects.filter(root__job=job):
            r.identifier = job.identifier + r.identifier[len_old:]
            r.save()
