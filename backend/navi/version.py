import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / "version.json"


def read_version(path=VERSION_FILE):
    try:
        value = json.loads(path.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return "0.0.0"
    return value if isinstance(value, str) and value.strip() else "0.0.0"


__version__ = read_version()
