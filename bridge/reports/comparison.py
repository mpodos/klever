#
# Copyright (c) 2018 ISP RAS (http://www.ispras.ru)
# Ivannikov Institute for System Programming of the Russian Academy of Sciences
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

import re
import json

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from bridge.vars import COMPARE_VERDICT
from bridge.utils import BridgeException

from users.models import User
from jobs.models import Job
from reports.models import AttrName, Attr, ReportAttr, ReportComponentLeaf, CompareJobsInfo, CompareJobsCache,\
    Report, ReportSafe, ReportUnsafe, ReportUnknown, ReportComponent

from marks.models import MarkUnsafeReport, MarkSafeReport, MarkUnknownReport

from jobs.utils import JobAccess
from marks.utils import UNSAFE_COLOR, SAFE_COLOR


def can_compare(user, job1, job2):
    if not isinstance(job1, Job) or not isinstance(job2, Job) or not isinstance(user, User):
        return False
    if not JobAccess(user, job1).can_view() or not JobAccess(user, job2).can_view():
        return False
    return True


class ReportTree:
    def __init__(self, root, name_ids):
        self._name_ids = name_ids
        self.attr_values = {}
        self._report_tree = {}
        self._leaves = {'u': set(), 's': set(), 'f': set()}
        self.__get_tree(root)

    def __get_tree(self, root):
        core_id = None
        for r_id, p_id in Report.objects.filter(root=root).values_list('id', 'parent_id'):
            self._report_tree[r_id] = p_id
            if p_id is None:
                core_id = r_id

        leaves_fields = {'u': 'unsafe_id', 's': 'safe_id', 'f': 'unknown_id'}
        # core has all leaves of the job
        for leaf in ReportComponentLeaf.objects.filter(report_id=core_id).values('safe_id', 'unsafe_id', 'unknown_id'):
            # There is often number of safes > unknowns > unsafes
            for l_type in 'sfu':
                l_id = leaf[leaves_fields[l_type]]
                if l_id is not None:
                    self._leaves[l_type].add(l_id)
                    break

        # The order is important
        for l_type in 'usf':
            self.__fill_leaves_vals(l_type)

    def __fill_leaves_vals(self, l_type):
        leaves_attrs = {}
        for ra in ReportAttr.objects.filter(report_id__in=self._leaves[l_type], attr__name_id__in=self._name_ids)\
                .select_related('attr').only('report_id', 'attr__name_id', 'attr__value'):
            if ra.report_id not in leaves_attrs:
                leaves_attrs[ra.report_id] = {}
            leaves_attrs[ra.report_id][ra.attr.name_id] = ra.attr_id

        for l_id in self._leaves[l_type]:
            if l_id in leaves_attrs:
                attrs_id = '|'.join(
                    str(leaves_attrs[l_id][n_id]) if n_id in leaves_attrs[l_id] else '-' for n_id in self._name_ids
                )
            else:
                attrs_id = '|'.join(['-'] * len(self._name_ids))

            branch_ids = [(l_type, l_id)]
            parent = self._report_tree[l_id]
            while parent is not None:
                branch_ids.insert(0, ('c', parent))
                parent = self._report_tree[parent]

            if attrs_id in self.attr_values:
                if l_type == 's':
                    self.attr_values[attrs_id]['verdict'] = COMPARE_VERDICT[5][0]
                elif l_type == 'f':
                    for branch in self.attr_values[attrs_id]['branches']:
                        if branch[-1][0] != 'u':
                            self.attr_values[attrs_id]['verdict'] = COMPARE_VERDICT[5][0]
                            break
                    else:
                        self.attr_values[attrs_id]['verdict'] = COMPARE_VERDICT[2][0]
                self.attr_values[attrs_id]['branches'].append(branch_ids)
            else:
                if l_type == 'u':
                    verdict = COMPARE_VERDICT[1][0]
                elif l_type == 's':
                    verdict = COMPARE_VERDICT[0][0]
                else:
                    verdict = COMPARE_VERDICT[3][0]
                self.attr_values[attrs_id] = {'branches': [branch_ids], 'verdict': verdict}


class CompareTree:
    def __init__(self, user, root1, root2):
        self.user = user
        self._root1 = root1
        self._root2 = root2

        self._name_ids = self.__get_attr_names()
        self.tree1 = ReportTree(self._root1, self._name_ids)
        self.tree2 = ReportTree(self._root2, self._name_ids)

        self.attr_values = {}
        self.__compare_values()
        self.__fill_cache()

    def __get_attr_names(self):
        names1 = set(aname for aname, in ReportAttr.objects.filter(report__root=self._root1, compare=True)
                     .values_list('attr__name_id'))
        names2 = set(aname for aname, in ReportAttr.objects.filter(report__root=self._root2, compare=True)
                     .values_list('attr__name_id'))
        if names1 != names2:
            raise BridgeException(_("Jobs with different sets of attributes to compare can't be compared"))
        return sorted(names1)

    def __compare_values(self):
        for a_id in self.tree1.attr_values:
            self.attr_values[a_id] = {
                'v1': self.tree1.attr_values[a_id]['verdict'],
                'v2': COMPARE_VERDICT[4][0],
                'branches1': self.tree1.attr_values[a_id]['branches'],
                'branches2': []
            }
            if a_id in self.tree2.attr_values:
                self.attr_values[a_id]['v2'] = self.tree2.attr_values[a_id]['verdict']
                self.attr_values[a_id]['branches2'] = self.tree2.attr_values[a_id]['branches']
        for a_id in self.tree2.attr_values:
            if a_id not in self.tree1.attr_values:
                self.attr_values[a_id] = {
                    'v1': COMPARE_VERDICT[4][0],
                    'v2': self.tree2.attr_values[a_id]['verdict'],
                    'branches1': [],
                    'branches2': self.tree2.attr_values[a_id]['branches']
                }

    def __fill_cache(self):
        info = CompareJobsInfo.objects.create(
            user=self.user, root1=self._root1, root2=self._root2,
            attr_names='|'.join(str(nid) for nid in self._name_ids)
        )
        CompareJobsCache.objects.bulk_create(list(CompareJobsCache(
            info=info, attr_values=x,
            verdict1=self.attr_values[x]['v1'], verdict2=self.attr_values[x]['v2'],
            reports1=json.dumps(self.attr_values[x]['branches1'], ensure_ascii=False),
            reports2=json.dumps(self.attr_values[x]['branches2'], ensure_ascii=False)
        ) for x in self.attr_values))


class ComparisonTableData:
    def __init__(self, user, root1, root2):
        self.data = []
        self.info = 0
        self.attrs = []
        self.__get_data(user, root1, root2)

    def __get_data(self, user, root1, root2):
        try:
            info = CompareJobsInfo.objects.get(user=user, root1=root1, root2=root2)
        except ObjectDoesNotExist:
            raise BridgeException(_('The comparison cache was not found'))
        self.info = info.pk

        numbers = {}
        for v1, v2, num in CompareJobsCache.objects.filter(info=info).values('verdict1', 'verdict2')\
                .annotate(number=Count('id')).values_list('verdict1', 'verdict2', 'number'):
            numbers[(v1, v2)] = num

        for v1 in COMPARE_VERDICT:
            row_data = []
            for v2 in COMPARE_VERDICT:
                num = '-'
                if (v1[0], v2[0]) in numbers:
                    num = (numbers[(v1[0], v2[0])], v2[0])
                row_data.append(num)
            self.data.append(row_data)

        all_attrs = {}
        names_ids = list(int(x) for x in info.attr_names.split('|'))
        for aname in AttrName.objects.filter(id__in=names_ids):
            all_attrs[aname.id] = {'values': set(), 'name': aname.name}
        if len(all_attrs) != len(names_ids):
            raise BridgeException(_('The comparison cache was corrupted'))

        for compare in info.comparejobscache_set.all():
            attr_values = compare.attr_values.split('|')
            if len(attr_values) != len(names_ids):
                raise BridgeException(_('The comparison cache was corrupted'))
            for i in range(len(attr_values)):
                if attr_values[i] == '-':
                    continue
                all_attrs[names_ids[i]]['values'].add(attr_values[i])

        for an_id in names_ids:
            values = []
            if len(all_attrs[an_id]['values']) > 0:
                values = list(Attr.objects.filter(id__in=all_attrs[an_id]['values'])
                              .order_by('value').values_list('id', 'value'))
            self.attrs.append({'name': all_attrs[an_id]['name'], 'values': values})


class ComparisonData:
    def __init__(self, info, page_num, hide_attrs, hide_components, verdict=None, attrs=None):
        self.info = info
        self._attr_names = list(int(x) for x in self.info.attr_names.split('|'))
        self.v1 = self.v2 = None
        self.hide_attrs = bool(int(hide_attrs))
        self.hide_components = bool(int(hide_components))
        self.attr_search = False
        self.pages = {
            'backward': True,
            'forward': True,
            'num': page_num,
            'total': 0
        }
        self.data = self.__get_data(verdict, attrs)

    def __get_verdicts(self, verdict):
        self.__is_not_used()
        m = re.match('^(\d)_(\d)$', verdict)
        if m is None:
            raise BridgeException()
        v1 = m.group(1)
        v2 = m.group(2)
        if any(v not in list(x[0] for x in COMPARE_VERDICT) for v in [v1, v2]):
            raise BridgeException()
        return v1, v2

    def __get_data(self, verdict=None, search_attrs=None):
        if search_attrs is not None:
            try:
                search_attrs = '|'.join(json.loads(search_attrs))
            except ValueError:
                raise BridgeException()
            if '__REGEXP_ANY__' in search_attrs:
                search_attrs = re.escape(search_attrs)
                search_attrs = search_attrs.replace('__REGEXP_ANY__', '\d+')
                search_attrs = '^' + search_attrs + '$'
                data = self.info.comparejobscache_set.filter(attr_values__regex=search_attrs).order_by('id')
            else:
                data = self.info.comparejobscache_set.filter(attr_values=search_attrs).order_by('id')
            self.attr_search = True
        elif verdict is not None:
            (v1, v2) = self.__get_verdicts(verdict)
            data = self.info.comparejobscache_set.filter(verdict1=v1, verdict2=v2).order_by('id')
        else:
            raise BridgeException()
        self.pages['total'] = len(data)
        if self.pages['total'] < self.pages['num']:
            raise BridgeException(_('Required reports were not found'))
        self.pages['backward'] = (self.pages['num'] > 1)
        self.pages['forward'] = (self.pages['num'] < self.pages['total'])
        data = data[self.pages['num'] - 1]
        for v in COMPARE_VERDICT:
            if data.verdict1 == v[0]:
                self.v1 = v[1]
            if data.verdict2 == v[0]:
                self.v2 = v[1]

        try:
            branches = self.__compare_reports(data)
        except ObjectDoesNotExist:
            raise BridgeException(_('The report was not found, please recalculate the comparison cache'))
        if branches is None:
            raise BridgeException()

        final_data = []
        for branch in branches:
            ordered = []
            for i in sorted(list(branch)):
                if len(branch[i]) > 0:
                    ordered.append(branch[i])
            final_data.append(ordered)
        return final_data

    def __compare_reports(self, c):
        data1 = self.__get_reports_data(json.loads(c.reports1))
        data2 = self.__get_reports_data(json.loads(c.reports2))
        for i in sorted(list(data1)):
            if i not in data2:
                break
            blocks = self.__compare_lists(data1[i], data2[i])
            if isinstance(blocks, list) and len(blocks) == 2:
                data1[i] = blocks[0]
                data2[i] = blocks[1]
        return [data1, data2]

    def __compare_lists(self, blocks1, blocks2):
        for b1 in blocks1:
            for b2 in blocks2:
                if b1.block_class != b2.block_class or b1.type == 'm':
                    continue
                for a1 in b1.list:
                    if a1['name'] not in list(x['name'] for x in b2.list):
                        a1['color'] = '#c60806'
                    for a2 in b2.list:
                        if a2['name'] not in list(x['name'] for x in b1.list):
                            a2['color'] = '#c60806'
                        if a1['name'] == a2['name'] and a1['value'] != a2['value']:
                            a1['color'] = a2['color'] = '#af49bd'
        if self.hide_attrs:
            for b1 in blocks1:
                for b2 in blocks2:
                    if b1.block_class != b2.block_class or b1.type == 'm':
                        continue
                    for b in [b1, b2]:
                        new_list = []
                        for a in b.list:
                            if 'color' in a:
                                new_list.append(a)
                        b.list = new_list
        if self.hide_components:
            for_del = {
                'b1': [],
                'b2': []
            }
            for i in range(len(blocks1)):
                for j in range(len(blocks2)):
                    if blocks1[i].block_class != blocks2[j].block_class or blocks1[i].type != 'c':
                        continue
                    if blocks1[i].list == blocks2[j].list and blocks1[i].add_info == blocks2[j].add_info:
                        for_del['b1'].append(i)
                        for_del['b2'].append(j)
            new_blocks1 = []
            for i in range(0, len(blocks1)):
                if i not in for_del['b1']:
                    new_blocks1.append(blocks1[i])
            new_blocks2 = []
            for i in range(0, len(blocks2)):
                if i not in for_del['b2']:
                    new_blocks2.append(blocks2[i])
            return [new_blocks1, new_blocks2]
        return None

    def __get_reports_data(self, reports):
        branch_data = {}
        get_block = {
            'u': (self.__unsafe_data, self.__unsafe_mark_data),
            's': (self.__safe_data, self.__safe_mark_data),
            'f': (self.__unknown_data, self.__unknown_mark_data)
        }
        added_ids = set()
        for branch in reports:
            cnt = 1
            parent = None
            for rdata in branch:
                if cnt not in branch_data:
                    branch_data[cnt] = []
                if rdata[1] in added_ids:
                    pass
                elif rdata[0] == 'c':
                    branch_data[cnt].append(
                        self.__component_data(rdata[1], parent)
                    )
                elif rdata[0] in 'usf':
                    branch_data[cnt].append(
                        get_block[rdata[0]][0](rdata[1], parent)
                    )
                    cnt += 1
                    for b in get_block[rdata[0]][1](rdata[1]):
                        if cnt not in branch_data:
                            branch_data[cnt] = []
                        if b.id not in list(x.id for x in branch_data[cnt]):
                            branch_data[cnt].append(b)
                        else:
                            for i in range(len(branch_data[cnt])):
                                if b.id == branch_data[cnt][i].id:
                                    if rdata[0] == 'f' \
                                            and b.add_info[0]['value'] != branch_data[cnt][i].add_info[0]['value']:
                                        branch_data[cnt].append(b)
                                    else:
                                        branch_data[cnt][i].parents.extend(b.parents)
                                    break
                    break
                parent = rdata[1]
                cnt += 1
                added_ids.add(rdata[1])
        return branch_data

    def __component_data(self, report_id, parent_id):
        report = ReportComponent.objects.get(pk=report_id)
        block = CompareBlock('c_%s' % report_id, 'c', report.component.name, 'comp_%s' % report.component_id)
        if parent_id is not None:
            block.parents.append('c_%s' % parent_id)
        block.list = self.__get_attrs_list(report)
        block.href = reverse('reports:component', args=[report.pk])
        return block

    def __unsafe_data(self, report_id, parent_id):
        report = ReportUnsafe.objects.get(pk=report_id)
        block = CompareBlock('u_%s' % report_id, 'u', _('Unsafe'), 'unsafe')
        block.parents.append('c_%s' % parent_id)
        block.add_info = {'value': report.get_verdict_display(), 'color': UNSAFE_COLOR[report.verdict]}
        block.list = self.__get_attrs_list(report)
        block.href = reverse('reports:unsafe', args=[report.trace_id])
        return block

    def __safe_data(self, report_id, parent_id):
        report = ReportSafe.objects.get(pk=report_id)
        block = CompareBlock('s_%s' % report_id, 's', _('Safe'), 'safe')
        block.parents.append('c_%s' % parent_id)
        block.add_info = {'value': report.get_verdict_display(), 'color': SAFE_COLOR[report.verdict]}
        block.list = self.__get_attrs_list(report)
        block.href = reverse('reports:safe', args=[report.pk])
        return block

    def __unknown_data(self, report_id, parent_id):
        report = ReportUnknown.objects.get(pk=report_id)
        block = CompareBlock('f_%s' % report_id, 'f', _('Unknown'), 'unknown-%s' % report.component.name)
        block.parents.append('c_%s' % parent_id)
        problems = list(x.problem.name for x in report.markreport_set.select_related('problem').order_by('id'))
        if len(problems) > 0:
            block.add_info = {'value': '; '.join(problems), 'color': '#c60806'}
        else:
            block.add_info = {'value': _('Without marks')}
        block.list = self.__get_attrs_list(report)
        block.href = reverse('reports:unknown', args=[report.pk])
        return block

    def __get_attrs_list(self, report):
        attrs_list = []
        for an_id, a_name, a_val in report.attrs.values_list('attr__name_id', 'attr__name__name', 'attr__value')\
                .order_by('attr__name__name'):
            attr_data = {'name': a_name, 'value': a_val}
            if an_id in self._attr_names:
                attr_data['color'] = '#8bb72c'
            attrs_list.append(attr_data)
        return attrs_list

    def __unsafe_mark_data(self, report_id):
        self.__is_not_used()
        blocks = []
        for mark in MarkUnsafeReport.objects.filter(report_id=report_id, result__gt=0, type__in='01')\
                .select_related('mark'):
            block = CompareBlock('um_%s' % mark.mark_id, 'm', _('Unsafes mark'))
            block.parents.append('u_%s' % report_id)
            block.add_info = {'value': mark.mark.get_verdict_display(), 'color': UNSAFE_COLOR[mark.mark.verdict]}
            block.href = reverse('marks:mark', args=['unsafe', mark.mark_id])
            for t in mark.mark.versions.order_by('-version').first().tags.all():
                block.list.append({'name': None, 'value': t.tag.tag})
            blocks.append(block)
        return blocks

    def __safe_mark_data(self, report_id):
        self.__is_not_used()
        blocks = []
        for mark in MarkSafeReport.objects.filter(report_id=report_id).select_related('mark'):
            block = CompareBlock('sm_%s' % mark.mark_id, 'm', _('Safes mark'))
            block.parents.append('s_%s' % report_id)
            block.add_info = {'value': mark.mark.get_verdict_display(), 'color': SAFE_COLOR[mark.mark.verdict]}
            block.href = reverse('marks:mark', args=['safe', mark.mark_id])
            for t in mark.mark.versions.order_by('-version').first().tags.all():
                block.list.append({'name': None, 'value': t.tag.tag})
            blocks.append(block)
        return blocks

    def __unknown_mark_data(self, report_id):
        self.__is_not_used()
        blocks = []
        for mark in MarkUnknownReport.objects.filter(report_id=report_id).select_related('problem'):
            block = CompareBlock("fm_%s" % mark.mark_id, 'm', _('Unknowns mark'))
            block.parents.append('f_%s' % report_id)
            block.add_info = {'value': mark.problem.name}
            block.href = reverse('marks:mark', args=['unknown', mark.mark_id])
            blocks.append(block)
        return blocks

    def __is_not_used(self):
        pass


class CompareBlock:
    def __init__(self, block_id, block_type, title, block_class=None):
        self.id = block_id
        self.block_class = block_class if block_class is not None else self.id
        self.type = block_type
        self.title = title
        self.parents = []
        self.list = []
        self.add_info = None
        self.href = None
