import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets

import uvicorn

from .gateway import Gateway
from .web import create_app


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("NAVI_ACCEPTANCE_DATA", ROOT / "data")).resolve()


def main():
    DATA.mkdir(exist_ok=True)
    os.chmod(DATA, 0o700)
    lock = (DATA / "gateway.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("本项目 Gateway 已在运行，请打开 http://127.0.0.1:8766")
    instance, token = secrets.token_hex(16), secrets.token_hex(32)
    project_id = hashlib.sha256((str(ROOT) if DATA == ROOT / "data" else str(ROOT) + ":" + str(DATA)).encode()).hexdigest()[:24]
    runtime = DATA / "runtime.json"
    descriptor = os.open(runtime, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"pid": os.getpid(), "instance": instance, "project_id": project_id, "token": token}, stream)
    gateway = Gateway(ROOT, DATA)
    app = create_app(gateway, instance, project_id, token)
    try:
        uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False, log_level="info")
    finally:
        try:
            if json.loads(runtime.read_text()).get("instance") == instance:
                runtime.unlink()
        except (OSError, ValueError):
            pass
        lock.close()


if __name__ == "__main__":
    main()
