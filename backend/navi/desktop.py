import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

from .launcher import DATA, ROOT, URL, health, owned
from .server import lock_exclusive


WINDOW_TITLE = "Navi"
WINDOW_STATE = DATA / "window.json"
DESKTOP_LOG = DATA / "desktop.log"
STOP_FLAG = DATA / "desktop.stop"


def log(message):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with DESKTOP_LOG.open("a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def show_error(message):
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Navi", 0x10)
    else:
        print(message, file=sys.stderr)


def activate_existing_window():
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    handle = user32.FindWindowW(None, WINDOW_TITLE)
    if not handle:
        return False
    user32.ShowWindow(handle, 9)
    ensure_handle_visible(handle)
    user32.SetForegroundWindow(handle)
    return True


def virtual_screen_bounds():
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:
        return None
    return left, top, width, height


def bounds_are_visible(left, top, width, height):
    bounds = virtual_screen_bounds()
    if bounds is None:
        return True
    screen_left, screen_top, screen_width, screen_height = bounds
    margin = 48
    return (
        left + width > screen_left + margin
        and left < screen_left + screen_width - margin
        and top + height > screen_top + margin
        and top < screen_top + screen_height - margin
    )


def ensure_handle_visible(handle):
    if os.name != "nt" or not handle:
        return
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    user32 = ctypes.windll.user32
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        return
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if bounds_are_visible(rect.left, rect.top, width, height):
        return
    bounds = virtual_screen_bounds()
    if bounds is None:
        return
    screen_left, screen_top, screen_width, screen_height = bounds
    left = screen_left + max(0, (screen_width - width) // 2)
    top = screen_top + max(0, (screen_height - height) // 2)
    user32.SetWindowPos(handle, 0, left, top, 0, 0, 0x0001 | 0x0004 | 0x0040)


def acquire_window_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    lock = (DATA / "window.lock").open("a+b")
    try:
        lock_exclusive(lock)
    except OSError:
        lock.close()
        return None
    return lock


def ensure_backend():
    state = health()
    if owned(state) and state.get("ready"):
        return
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "PYTHONUTF8": "1"}
    completed = subprocess.run(
        [sys.executable, "-m", "navi.launcher", "start", "--no-browser"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Navi 后台启动失败。请查看 {DATA / 'gateway.log'}")
    for _ in range(100):
        state = health()
        if owned(state) and state.get("ready"):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Navi 后台尚未就绪。请查看 {DATA / 'gateway.log'}")


def load_geometry():
    geometry = {"width": 1280, "height": 820}
    try:
        saved = json.loads(WINDOW_STATE.read_text())
    except (OSError, ValueError):
        return geometry
    for key, minimum in (("width", 900), ("height", 600)):
        value = saved.get(key)
        if isinstance(value, int) and value >= minimum:
            geometry[key] = value
    x, y = saved.get("x"), saved.get("y")
    if isinstance(x, int) and isinstance(y, int) and bounds_are_visible(x, y, geometry["width"], geometry["height"]):
        geometry["x"] = x
        geometry["y"] = y
    if isinstance(saved.get("maximized"), bool):
        geometry["maximized"] = saved["maximized"]
    return geometry


def window_is_maximized():
    if os.name != "nt":
        return False
    handle = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
    return bool(handle and ctypes.windll.user32.IsZoomed(handle))


def save_geometry(window):
    try:
        width, height = window.width, window.height
        state = {"width": width, "height": height, "maximized": window_is_maximized()}
        x, y = window.x, window.y
        if bounds_are_visible(x, y, width, height):
            state["x"], state["y"] = x, y
        WINDOW_STATE.write_text(json.dumps(state, separators=(",", ":")))
    except (OSError, TypeError, ValueError):
        pass


def focus_window():
    if os.name != "nt":
        return
    handle = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
    if handle:
        ctypes.windll.user32.ShowWindow(handle, 9)
        ensure_handle_visible(handle)
        ctypes.windll.user32.SetForegroundWindow(handle)


def stop_backend():
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "PYTHONUTF8": "1"}
    subprocess.run(
        [sys.executable, "-m", "navi.launcher", "stop"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )


def create_tray(window, exiting):
    import pystray
    from PIL import Image

    icon_path = ROOT / "windows" / "navi.ico"
    image = Image.open(icon_path).convert("RGBA")
    finished = threading.Event()
    backend_state = {"ready": True}

    def open_window(icon=None, item=None):
        try:
            window.show()
            window.restore()
            focus_window()
        except Exception as error:
            log(f"tray open failed: {error}")

    def exit_navi(icon=None, item=None):
        if exiting.is_set():
            return
        exiting.set()
        finished.set()
        save_geometry(window)
        stop_backend()
        try:
            window.destroy()
        except Exception as error:
            log(f"tray destroy failed: {error}")
        if icon is not None:
            icon.stop()

    def status_text(item=None):
        return "状态：后台运行中" if backend_state["ready"] else "状态：后台未连接"

    tray = pystray.Icon(
        "Navi",
        image,
        "Navi · 后台运行中",
        pystray.Menu(
            pystray.MenuItem("打开 Navi", open_window, default=True),
            pystray.MenuItem(status_text, lambda icon, item: None, enabled=False),
            pystray.MenuItem("退出 Navi", exit_navi),
        ),
    )

    def monitor():
        while not finished.wait(1):
            if STOP_FLAG.exists():
                try:
                    STOP_FLAG.unlink()
                except OSError:
                    pass
                exiting.set()
                finished.set()
                save_geometry(window)
                try:
                    window.destroy()
                except Exception as error:
                    log(f"tray stop failed: {error}")
                tray.stop()
                return
            state = health()
            ready = bool(owned(state) and state.get("ready"))
            backend_state["ready"] = ready
            tray.title = "Navi · 后台运行中" if ready else "Navi · 后台未连接"

    thread = threading.Thread(target=monitor, name="navi-tray-monitor", daemon=True)
    thread.start()
    return tray, finished


def run_window():
    try:
        import webview
    except ImportError as error:
        raise RuntimeError("缺少桌面窗口组件，请重新运行 setup-windows.cmd。") from error
    geometry = load_geometry()
    window = webview.create_window(
        WINDOW_TITLE,
        URL,
        width=geometry["width"],
        height=geometry["height"],
        x=geometry.get("x"),
        y=geometry.get("y"),
        resizable=True,
        min_size=(900, 600),
        confirm_close=False,
        text_select=True,
        zoomable=True,
        maximized=geometry.get("maximized", False),
        background_color="#f7f6f3",
    )
    exiting = threading.Event()
    hidden_notified = threading.Event()
    tray = None
    tray_finished = None
    try:
        tray, tray_finished = create_tray(window, exiting)
    except Exception as error:
        log(f"tray unavailable: {error}")

    def closing():
        save_geometry(window)
        if exiting.is_set() or tray is None:
            return True
        window.hide()
        if not hidden_notified.is_set():
            hidden_notified.set()
            try:
                tray.notify("Navi 继续在后台运行，可从右下角托盘重新打开。", "Navi")
            except Exception:
                pass
        return False

    window.events.closing += closing
    storage = DATA / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    icon = ROOT / "windows" / "navi.ico"
    try:
        if tray is not None:
            def tray_setup(icon):
                icon.visible = True
                log("tray icon visible")

            tray.run_detached(tray_setup)
        webview.start(private_mode=False, storage_path=str(storage), icon=str(icon) if icon.exists() else None)
    finally:
        exiting.set()
        if tray_finished is not None:
            tray_finished.set()
        if tray is not None:
            tray.stop()


def main():
    log("desktop launcher started")
    if activate_existing_window():
        log("activated existing window")
        return
    lock = acquire_window_lock()
    if lock is None:
        activate_existing_window()
        return
    try:
        ensure_backend()
        run_window()
    except Exception as error:
        log(f"desktop launcher failed: {error}\n{traceback.format_exc()}")
        show_error(str(error))


if __name__ == "__main__":
    main()
