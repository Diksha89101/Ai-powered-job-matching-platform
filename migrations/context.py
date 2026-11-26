from pathlib import Path
from runpy import run_path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_APP_NAMESPACE = run_path(str(_PROJECT_ROOT / 'app.py'))
app = _APP_NAMESPACE['app']
mongo = _APP_NAMESPACE['mongo']
