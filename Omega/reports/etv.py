import re
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import ugettext_lazy as _
from Omega.utils import print_err
from reports.models import ReportUnsafe
from reports.graphml_parser import GraphMLParser


TAB_LENGTH = 4
SOURCE_CLASSES = {
    'comment': "ETVSrcC",
    'number': "ETVSrcN",
    'line': "ETVSrcL",
    'text': "ETVSrcT",
    'key1': "ETVSrcI",
    'key2': "ETVSrcO"
}

KEY1_WORDS = [
    '#ifndef', '#elif', '#undef', '#ifdef', '#include', '#else', '#define',
    '#if', '#pragma', '#error', '#endif', '#line'
]

KEY2_WORDS = [
    '__based', 'static', 'if', 'sizeof', 'double', 'typedef', 'unsigned', 'new',
    'this', 'break', 'inline', 'explicit', 'template', 'bool', 'for', 'private',
    'default', 'else', 'const', '__pascal', 'delete', 'class', 'continue', 'do',
    '__fastcall', 'union', 'extern', '__cdecl', 'friend', '__inline', 'int',
    '__virtual_inheritance', 'void', 'case', '__multiple_inheritance', 'enum',
    'short', 'operator', '__asm', 'float', 'struct', 'cout', 'public', 'auto',
    'long', 'goto', '__single_inheritance', 'volatile', 'throw', 'namespace',
    'protected', 'virtual', 'return', 'signed', 'register', 'while', 'try',
    'switch', 'char', 'catch', 'cerr', 'cin'
]


class GetETV(object):
    def __init__(self, report):
        self.report = report
        self.error = None

        self.g = self.__parse_graph()
        if self.error is not None:
            return

        self.traces = self.__get_traces()
        if self.error is not None:
            return

        if len(self.traces) == 0:
            self.error = _('Wrong error trace file format')
            return
        elif len(self.traces) > 2:
            self.error = _('Error trace with more than two '
                           'error pathes are not supported')
            return

        self.html_traces = []

        for trace in self.traces:
            self.__html_trace(trace)

    def __parse_graph(self):
        try:
            return GraphMLParser().parse(self.report.error_trace_processed)
        except Exception as e:
            print_err(e)
            self.error = _('Wrong error trace file format')
        return None

    def __get_traces(self):
        traces = []
        if self.g.set_root_by_attribute('true', 'isEntryNode') is None:
            self.error = _('Trace entry was not found')
            return traces

        for path in self.g.bfs():
            if 'isSinkNode' not in path[-1].attr or path[-1]['isSinkNode'] != 'true':
                traces.append(path)
        return traces

    def __html_trace(self, node_trace):
        max_line_length = 1

        edge_trace = []
        prev_node = node_trace[0]
        for n in node_trace[1:]:
            e = self.g.edge(prev_node, n)
            if e is not None:
                edge_trace.append(e)
            prev_node = n

        for n in edge_trace:
            if 'startline' in n.attr:
                if len(n['startline']) > max_line_length:
                    max_line_length = len(n['startline'])
        assume_scopes = {}
        curr_scope = 'global'

        def get_line(c, l, f, o, sc):
            if curr_scope not in assume_scopes:
                assume_scopes[curr_scope] = []
            if l is not None:
                fileinput = '<input type="hidden" value="%s">' % f
                source_href = '<a href="#" class="ETV_La">%s</a>' % l
                line_length = len(l)
            else:
                source_href = ''
                fileinput = ''
                line_length = 0
            linadata = '<span class="ETVSrcL">%s%s%s</span>' % (
                ' ' * (max_line_length - line_length), source_href, fileinput
            )
            return '<span class="%s">%s%s %s%s</span><br>' % (
                c, linadata, ' ' * o, sc, ''.join(assume_scopes[curr_scope])
            )

        trace = ''
        cnt = 1
        num_of_enters = 0
        curr_offset = 0
        file = None
        has_main = False
        for n in edge_trace:
            line = None

            attrs = []
            for a in n.attr:
                attrs.append("%s: %s" % (a, n.attr[a].value))
            if 'startline' in n.attr:
                line = n['startline']
            if 'originFileName' in n.attr:
                file = n['originFileName']
            if file is None:
                line = None
            sourcecode = ''
            if 'sourcecode' in n.attr:
                sourcecode = n['sourcecode']

            if 'assumption' in n.attr:
                if 'assumption.scope' in n.attr:
                    curr_scope = n['assumption.scope']
                if not has_main and curr_scope == 'main':
                    num_of_enters += 1
                    cnt += 1
                    hide_link = '<a href="#" class="ETV_Fa">-</a>'
                    trace += get_line(
                        'ETV_F', None, None, curr_offset, hide_link + 'main()'
                    )
                    trace += get_line(
                        '', None, None, curr_offset, '{'
                    )
                    curr_offset += TAB_LENGTH
                    trace += '<span id="%s__%s">' % ('main', str(cnt))
                    has_main = True
                for assume in n['assumption'].split(';'):
                    if len(assume) == 0:
                        continue
                    if curr_scope not in assume_scopes:
                        assume_scopes[curr_scope] = []
                    assume_scopes[curr_scope].append(
                        '<input class="ETV_ScopeAssume" type="hidden" value="%s">' % assume
                    )
                trace += get_line('ETV_A', line, file, curr_offset, sourcecode)
            elif 'enterFunction' in n.attr:
                num_of_enters += 1
                cnt += 1
                hide_link = '<a href="#" class="ETV_Fa">-</a>'
                trace += get_line(
                    'ETV_F', line, file, curr_offset, hide_link + sourcecode
                )
                trace += get_line(
                    '', None, None, curr_offset, '{'
                )
                curr_offset += TAB_LENGTH
                trace += '<span id="%s__%s">' % (n['enterFunction'], str(cnt))
            elif 'returnFromFunction' in n.attr:
                trace += get_line(
                    'ETV_F', line, file, curr_offset, sourcecode
                )
                if curr_offset >= TAB_LENGTH:
                    curr_offset -= TAB_LENGTH
                trace += get_line(
                    '', None, None, curr_offset, '}'
                )
                trace += '</span>'
            else:
                trace += get_line(
                    '', line, file, curr_offset, sourcecode
                )
        for i in range(0, num_of_enters // TAB_LENGTH + 1):
            if curr_offset >= TAB_LENGTH:
                curr_offset -= TAB_LENGTH
            trace += get_line('', None, None, curr_offset, '}')
            trace += '</span>'
        trace += get_line('', None, None, 0, '')
        self.html_traces.append(trace)


class GetSource(object):
    def __init__(self, report_id, file_name):
        self.error = None
        self.report = self.__get_report(report_id)
        if self.error is not None:
            return
        self.is_comment = False
        self.is_text = False
        self.text_quote = None
        self.data = self.__get_source(file_name)

    def __get_report(self, report_id):
        try:
            return ReportUnsafe.objects.get(pk=int(report_id))
        except ObjectDoesNotExist:
            self.error = _("Report was not found")
            return None
        except ValueError:
            self.error = _("Unknown error")
            return None

    def __get_source(self, file_name):
        data = ''
        if file_name is None:
            self.error = _("Source was not found")
            return
        f = open(file_name, 'r')
        cnt = 1
        lines = f.read().split('\n')
        for line in lines:
            line = line.replace('\t', ' ' * TAB_LENGTH)
            line_num = ' ' * (len(str(len(lines))) - len(str(cnt))) + str(cnt)
            data += '<span>%s %s</span><br>' % (
                self.__wrap_line(line_num, 'line'), self.__parse_line(line)
            )
            cnt += 1
        f.close()
        return data

    def __parse_line(self, line):
        if self.is_comment:
            m = re.match('(.*?)\*/(.*)', line)
            if m is None:
                return self.__wrap_line(line, 'comment')
            self.is_comment = False
            new_line = self.__wrap_line(m.group(1) + '*/', 'comment')
            return new_line + self.__parse_line(m.group(2))

        if self.is_text:
            before, after = self.__parse_text(line)
            if after is None:
                return self.__wrap_line(before, 'text')
            self.is_text = False
            return self.__wrap_line(before, 'text') + self.__parse_line(after)

        m = re.match('(.*?)/\*(.*)', line)
        if m is not None:
            new_line = self.__parse_line(m.group(1))
            self.is_comment = True
            new_line += self.__parse_line('/*' + m.group(2))
            return new_line
        m = re.match('(.*?)//(.*)', line)
        if m is not None:
            new_line = self.__parse_line(m.group(1))
            new_line += self.__wrap_line('//' + m.group(2), 'comment')
            return new_line
        m = re.match('(.*?)"(.*)', line)
        if m is not None:
            new_line = self.__parse_line(m.group(1))
            self.text_quote = '"'
            before, after = self.__parse_text(m.group(2))
            new_line += self.__wrap_line('"' + before, 'text')
            if after is None:
                self.is_text = True
                return new_line
            self.is_text = False
            return new_line + self.__parse_line(after)
        m = re.match("(.*?)'(.*)", line)
        if m is not None:
            new_line = self.__parse_line(m.group(1))
            self.text_quote = "'"
            before, after = self.__parse_text(m.group(2))
            new_line += self.__wrap_line("'" + before, 'text')
            if after is None:
                self.is_text = True
                return new_line
            self.is_text = False
            return new_line + self.__parse_line(after)
        m = re.match("(.*\W)(\d+)(\W.*)", line)
        if m is not None:
            new_line = self.__parse_line(m.group(1))
            new_line += self.__wrap_line(m.group(2), 'number')
            new_line += self.__parse_line(m.group(3))
            return new_line
        words = re.split('([^a-zA-Z0-9-_#])', line)
        new_words = []
        for word in words:
            if word in KEY1_WORDS:
                new_words.append(self.__wrap_line(word, 'key1'))
            elif word in KEY2_WORDS:
                new_words.append(self.__wrap_line(word, 'key2'))
            else:
                new_words.append(word)
        return ''.join(new_words)

    def __parse_text(self, text):
        escaped = False
        before = ''
        after = ''
        end_found = False
        for c in text:
            if end_found:
                after += c
                continue
            if not escaped and c == self.text_quote:
                end_found = True
            elif escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            before += c
        if end_found:
            return before, after
        return before, None

    def __wrap_line(self, line, text_type):
        self.ccc = 0
        if text_type not in SOURCE_CLASSES:
            return line
        return '<span class="%s">%s</span>' % (SOURCE_CLASSES[text_type], line)


def is_tag(tag, name):
    return bool(re.match('^({.*})*' + name + '$', tag))
