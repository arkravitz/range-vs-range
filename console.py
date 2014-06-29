"""
Runs an AdminCmd. Useful on PythonAnywhere.

Run this directly from your range-vs-range clone folder.

Make sure to switch to your virtualenv first, e.g. "workon rvr".
"""
import logging
from rvr.core.admin import AdminCmd
from rvr.mail.notifications import NOTIFICATION_SETTINGS
from rvr.app import APP
from rvr.local_settings import SERVER_NAME
from rvr.views import main, ajax, range_editor  # @UnusedImport

logging.basicConfig()
logging.root.setLevel(logging.DEBUG)

APP.SERVER_NAME = SERVER_NAME

with APP.app_context():
    NOTIFICATION_SETTINGS.suppress_email = False
    NOTIFICATION_SETTINGS.async_email = False
    
    CMD = AdminCmd()
    CMD.prompt = "> "
    CMD.cmdloop("Range vs. Range admin tool. Type ? for help.")