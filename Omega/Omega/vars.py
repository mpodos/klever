from django.utils.translation import ugettext_lazy as _

FORMAT = 1

JOB_CLASSES = (
    ('0', _('Verification of Linux kernel modules')),
    ('1', _('Verification of commits in Linux kernel Git repositories')),
    ('2', _('Verification of C programs')),
)

JOB_ROLES = (
    ('0', _('No access')),
    ('1', _('Observer')),
    ('2', _('Expert')),
    ('3', _('Observer and Operator')),
    ('4', _('Expert and Operator')),
)

# Default view of the table
JOB_DEF_VIEW = {
    'columns': ['name', 'role', 'date', 'status', 'unsafe', 'problem',
                'safe', 'resource'],
    # Available orders: ['date', 'status', 'name', 'author']
    'orders': ['-date'],

    # Available filters (id [types], (example value)):
    # name [iexact, istartswith, icontains] (<any text>)
    # change_author [is] (<id in the table>)
    # change_date [younger, older] (weeks|days|hours|minutes: <number>)
    # status [is, isnot] (<status identifier>)
    # resource:component [iexact, istartswith, icontains] (<any text>)
    # problem:component [iexact, istartswith, icontains] (<any text>)
    # problem:problem [iexact, istartswith, icontains] (<any text>)
    # format [is] (<number>)
    'filters': {
        # 'name': {
        #     'type': 'istartswith',
        #     'value': 'Title of the job',
        # },
        # 'change_author': {
        #     'type': 'is',
        #     'value': '1',
        # },
        # 'change_date': {
        #     'type': 'younger',
        #     'value': 'weeks:2',
        # },
        # 'resource_component': {
        #     'type': 'istartswith',
        #     'value': 'D',
        # },
        # 'problem_problem': {
        #     'type': 'icontains',
        #     'value': '1',
        # },
        # 'format': {
        #     'type': 'is',
        #     'value': '1',
        # },
    },
}

LANGUAGES = (
    ('en', 'English'),
    ('ru', 'Русский'),
)

USER_ROLES = (
    ('0', _('No access')),
    ('1', _('Producer')),
    ('2', _('Manager'))
)

# TODO: this doesn't need translation as well as english aliases. May be make comments instead.
VIEW_TYPES = {
    ('1', _('__Job tree')),
    ('2', _('__Other')),
}

JOB_STATUS = (
    ('0', _('Not solved')),
    ('1', _('Is solving')),
    ('2', _('Stopped')),
    ('3', _('Solved')),
    ('4', _('Failed')),
)

MARK_STATUS = (
    ('0', _('Unreported')),
    ('1', _('Reported')),
    ('2', _('Fixed')),
    ('3', _('Rejected')),
)
