###################################################################
#  This file serves as a base configuration for testing purposes  #
#  only. It is not intended for production use.                   #
###################################################################

from netbox_branching.utilities import DynamicSchemaDict

ALLOWED_HOSTS = ['*']

# netbox-branching requires DATABASES (not DATABASE) to be a DynamicSchemaDict.
DATABASES = DynamicSchemaDict(
    {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'netbox',
            'USER': 'netbox',
            'PASSWORD': 'netbox',
            'HOST': 'localhost',
            'PORT': '',
            'CONN_MAX_AGE': 300,
            # Own test database, so concurrent suites of other projects never collide.
            'TEST': {
                'NAME': 'test_netbox_custom_objects',
            },
        }
    }
)

DATABASE_ROUTERS = ['netbox_branching.database.BranchAwareRouter']

PLUGINS = [
    'netbox_custom_objects',
    'netbox_branching',
]

REDIS = {
    'tasks': {
        'HOST': 'localhost',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 0,
        'SSL': False,
    },
    'caching': {
        'HOST': 'localhost',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 1,
        'SSL': False,
    },
}

SECRET_KEY = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

API_TOKEN_PEPPERS = {
    1: 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
}

# NetBox's manage.py makemigrations refuses to run in non-developer setups.
# This testing config is dev/test only; safe to enable unconditionally.
DEVELOPER = True

DEBUG_TOOLBAR_CONFIG = {
    'IS_RUNNING_TESTS': False,
}
