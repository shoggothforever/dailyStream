source .venv/bin/activate && PYTHONPATH=src python -c "
import os, logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser('~/.dailystream/app.log')),
    ]
)

from dailystream.app import run_app
import traceback
try:
    run_app()
except Exception as e:
    traceback.print_exc()
" 2>&1 | grep -v 'TSM AdjustCapsLock\|IMKCFRunLoopWakeUp'