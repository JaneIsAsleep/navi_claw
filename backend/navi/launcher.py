import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("NAVI_ACCEPTANCE_DATA", ROOT / "data")).resolve()
URL = "http://127.0.0.1:8766"
PROJECT = hashlib.sha256((str(ROOT) if DATA == ROOT / "data" else str(ROOT) + ":" + str(DATA)).encode()).hexdigest()[:24]


def health():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def owned(state):
    return state and state.get("name") == "Navi" and state.get("project_id") == PROJECT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["start", "stop", "status"])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    state = health()
    if args.operation == "status":
        print("Navi 正在运行" if owned(state) and state["ready"] else "Navi 未运行" if state is None else "8766 端口被其他服务占用")
        print(f"入口：{URL}\n日志：{DATA / 'gateway.log'}")
        return
    if args.operation == "stop":
        if not owned(state):
            print("本项目 Navi 未运行。")
            return
        try:
            runtime = json.loads((DATA / "runtime.json").read_text())
        except (OSError, ValueError):
            raise SystemExit("缺少本项目运行凭证，未停止任何程序。")
        if runtime["instance"] != state["instance"] or runtime["pid"] != state["pid"]:
            raise SystemExit("运行身份不一致，未停止任何程序。")
        request = urllib.request.Request(URL + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json", "X-Navi-Owner": runtime["token"]}, method="POST")
        with urllib.request.urlopen(request, timeout=3):
            pass
        for _ in range(100):
            time.sleep(0.1)
            current = health()
            if current is None or current.get("instance") != state["instance"]:
                try:
                    os.kill(state["pid"], 0)
                except ProcessLookupError:
                    print("Navi 已正常停止，数据已保存。")
                    return
                except PermissionError:
                    pass
        raise SystemExit("停止尚未完成，请查看状态与日志。")
    if state and not owned(state):
        raise SystemExit("8766 端口被其他程序占用，未终止该程序。请释放端口后重试。")
    if not state:
        try:
            with socket.create_connection(("127.0.0.1", 8766), timeout=1):
                raise SystemExit("8766 端口被其他程序占用，未终止该程序。请释放端口后重试。")
        except OSError:
            pass
        if not (ROOT / "frontend/dist/index.html").exists():
            raise SystemExit("请先在项目目录执行 ./setup，准备依赖和前端。")
        DATA.mkdir(exist_ok=True)
        os.chmod(DATA, 0o700)
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
        with (DATA / "gateway.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-m", "navi.server"], cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
        for _ in range(150):
            state = health()
            if owned(state) and state["ready"]:
                break
            if process.poll() is not None:
                raise SystemExit(f"启动失败，请查看 {DATA / 'gateway.log'}")
            time.sleep(0.1)
        else:
            raise SystemExit(f"启动未就绪，请查看 {DATA / 'gateway.log'}")
    if not state["ready"]:
        raise SystemExit("Gateway 正在准备，请稍后使用 status.command。")
    if not args.no_browser:
        webbrowser.open(URL)
    print(f"Navi 已就绪：{URL}\n此终端窗口可以关闭，后台会继续运行。\n真正停止请双击 stop.command。")


if __name__ == "__main__":
    main()
