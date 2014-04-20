# pylint: disable=W0401
# Wildcard import
from oioioi.default_settings import *
from oioioi.base.config_version_check import INSTALLATION_CONFIG_VERSION

CONFIG_VERSION = INSTALLATION_CONFIG_VERSION

TIME_ZONE = 'UTC'

ADMINS = (
    ('Test admin', 'admin@example.com'),
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
    }
}

# Enable optional modules.
INSTALLED_APPS = (
    'oioioi.contestlogo',
    'oioioi.teachers',
    'oioioi.dashboard',
    'oioioi.clock',
    'oioioi.ipdnsauth',
    'oioioi.participants',
    'oioioi.oi',
    'oioioi.printing',
    'oioioi.zeus',
    'oioioi.testrun',
    'oioioi.scoresreveal',
    'oioioi.oireports',
    'oioioi.oisubmit',
    'oioioi.complaints',
    'oioioi.contestexcl',
    'oioioi.forum',
    'oioioi.confirmations',
    'oioioi.ctimes',
    'oioioi.acm',
    'oioioi.suspendjudge',
    'oioioi.submitsqueue',
    'oioioi.timeline',
    'oioioi.statistics',
    'oioioi.amppz',
    'oioioi.publicsolutions',
    'oioioi.testspackages',
    'oioioi.teams',
    'oioioi.pa'
) + INSTALLED_APPS

TEMPLATE_CONTEXT_PROCESSORS += (
    'oioioi.contestlogo.processors.logo_processor',
    'oioioi.contestlogo.processors.icon_processor',
)

AUTHENTICATION_BACKENDS += (
    'oioioi.base.tests.IgnorePasswordAuthBackend',
    'oioioi.teachers.auth.TeacherAuthBackend',
)

MIDDLEWARE_CLASSES += (
    'oioioi.base.tests.FakeTimeMiddleware',
)

TESTS = True

NOSE_PLUGINS = [
    'oioioi.base.tests.nose_plugins.DisableSouthDebugMessages',
    'oioioi.base.tests.nose_plugins.ClearCache',
]

SECRET_KEY = 'no_secret'
OISUBMIT_MAGICKEY = 'abcdef'
COMPRESS_ENABLED = False
COMPRESS_PRECOMPILERS = ()
CELERY_ALWAYS_EAGER = True
SIOWORKERS_BACKEND = 'oioioi.sioworkers.backends.LocalBackend'
FILETRACKER_CLIENT_FACTORY = 'filetracker.dummy.DummyClient'
USE_UNSAFE_EXEC = True
USE_LOCAL_COMPILERS = True

USE_SINOLPACK_MAKEFILES = True


COMPLAINTS_EMAIL = 'dummy@example.com'
COMPLAINTS_SUBJECT_PREFIX = '[oioioi-complaints] '

WARN_ABOUT_REPEATED_SUBMISSION = False

PROBLEM_SOURCES += (
    'oioioi.zeus.problem_sources.ZeusProblemSource',
)

ZEUS_INSTANCES = {
    'zeus_correct': ('__use_object__',
                     'oioioi.zeus.tests.ZeusCorrectServer', ''),
    'zeus_incorrect': ('__use_object__',
                       'oioioi.zeus.tests.ZeusIncorrectServer', ''),
    'dummy': ('__use_object__',
                   'oioioi.zeus.tests.ZeusDummyServer', ''),
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'
    }
}
