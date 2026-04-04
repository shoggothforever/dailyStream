cd /Users/yiths/CodeBuddy/dailyStream && source .venv/bin/activate && PYTHONPATH=src python -c "
import os, sys, logging

# Suppress macOS TSM/IMK stderr warnings (harmless noise from input method system)
import subprocess

import AppKit
ns_app = AppKit.NSApplication.sharedApplication()
ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

# Set up logging so real errors go to file, not lost in noise
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser('~/.dailystream/app.log')),
    ]
)

from dailystream.app import DailyStreamApp
import traceback
try:
    app = DailyStreamApp()
    print('App created OK, calling run()...')
    app.run()
except Exception as e:
    traceback.print_exc()
" 2>&1 | grep -v 'TSM AdjustCapsLock\|IMKCFRunLoopWakeUp'