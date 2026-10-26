"""展覽啟動器主流程（架構文件 2.4）。

1. 單一執行個體鎖；第二個執行個體只要求補開展示視窗後結束。
2. 在程序內啟動集中日誌寫入器，之後所有程序的日誌都經由它寫入 runtime.txt。
3. 檢查執行環境；致命問題（連線埠被占用等）記錄後以狀態頁說明並結束。
4. 啟動 Web 與 Worker 子程序，輪詢 /api/health 等待就緒。
5. 開啟左／中／右三個 kiosk 展示視窗，持續監看子程序、視窗與 launcher_requests。
6. 收到結束要求或 Ctrl+C 時關閉自有視窗與子程序，作品與佇列保留。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import DISPLAY_ROLES, Paths, get_paths, http_host, http_port, log_sink_address, read_displays_config
from backend.logclient import get_logger
from backend.logsink.server import DEFAULT_PORT as DEFAULT_LOG_PORT
from backend.logsink.server import LogSinkServer
from backend.platform.monitors import list_monitors
from launcher import browser
from launcher.health import HealthClient, frontend_missing, is_ready, pending_requests
from launcher.processes import ChildSpec, ManagedChild, child_env, console_attached, resolve_child_python
from launcher.single_instance import SingleInstance, instance_name
from launcher.status_page import write_status_page

CODE_ROOT = Path(__file__).resolve().parent.parent
_HEALTH_INTERVAL = 2.0
_WINDOW_CHECK_INTERVAL = 5.0
_WINDOW_LAUNCH_GRACE = 8.0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    configured_sink = log_sink_address()
    parser = argparse.ArgumentParser(prog="python -m launcher", description="人工對畫(話) 展覽啟動器")
    parser.add_argument("--no-displays", action="store_true", help="不開啟展示視窗（無螢幕驗證用）")
    parser.add_argument("--port", type=int, default=None, help="Web 連線埠，預設 AP_DRAWART_PORT 或 8080")
    parser.add_argument(
        "--log-port",
        type=int,
        default=configured_sink[1] if configured_sink else DEFAULT_LOG_PORT,
        help="集中日誌寫入器連線埠；被占用時改用系統分配的連線埠",
    )
    parser.add_argument("--health-timeout", type=float, default=60.0, help="等待 Web 就緒的秒數")
    return parser.parse_args(argv)


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


@dataclass
class DisplayWindow:
    role: str
    profile_dir: Path
    monitor: dict[str, Any] | None
    url: str = ""
    launched_at: float = 0.0
    reopen_count: int = 0


class Launcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        if args.port is not None:
            os.environ["AP_DRAWART_PORT"] = str(args.port)
        self.paths: Paths = get_paths()
        self.port = http_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.health = HealthClient(self.base_url)
        self.instance = SingleInstance(instance_name(self.paths.data_dir))
        self.sink: LogSinkServer | None = None
        self.log = get_logger("launcher")
        self.children: list[ManagedChild] = []
        self.windows: dict[str, DisplayWindow] = {}
        self.display_mode = "off"  # off / status / live
        self.ready = False
        self.last_health: dict[str, Any] | None = None
        self.stop_reason: str | None = None
        self.keep_status_windows = False
        self._shut_down = False

    # ---- 進入點 ----

    def run(self) -> int:
        if not self.instance.acquire():
            return self._run_second_instance()
        try:
            return self._run()
        except KeyboardInterrupt:
            self.stop_reason = "收到中斷訊號"
            return 0
        finally:
            self._shutdown()

    def _run_second_instance(self) -> int:
        """展覽已在執行：沿用既有服務與佇列，只要求補開展示視窗。"""
        os.environ["AP_DRAWART_LOG_SINK"] = f"127.0.0.1:{self.args.log_port}"
        if self.args.no_displays:
            message = "展覽系統已在執行，未要求補開展示視窗"
            ok = self.health.fetch() is not None
        else:
            message = "展覽系統已在執行，已要求補開展示視窗"
            ok = self.health.post("/api/admin/displays/relaunch")
        if ok:
            self.log.info("system.launcher_second_instance", message)
        else:
            self.log.warning("system.launcher_second_instance", "展覽系統已在執行，但目前無法連線到 Web 服務")
        if sys.stdout is not None:
            print(message if ok else "展覽系統已在執行，但目前無法連線到 Web 服務")
        return 0

    def _run(self) -> int:
        self._start_log_sink()
        self.log.info(
            "system.launcher_start",
            "啟動器開始",
            root=str(self.paths.root),
            port=self.port,
            log_sink=self.sink.address if self.sink else None,
            displays=not self.args.no_displays,
        )

        fatal = self._preflight()
        if fatal:
            self._show_status("無法開展", fatal[0], fatal[1:])
            # 狀態頁留在展示螢幕上，供現場人員看到原因
            self.keep_status_windows = True
            return 2

        self._start_children()
        self._wait_until_ready()
        return self._supervise()

    # ---- 啟動步驟 ----

    def _start_log_sink(self) -> None:
        for port in (self.args.log_port, 0):
            sink = LogSinkServer("127.0.0.1", port, self.paths.runtime_log)
            try:
                sink.start()
            except OSError:
                continue
            self.sink = sink
            os.environ["AP_DRAWART_LOG_SINK"] = sink.address
            if port != self.args.log_port:
                self.log.warning(
                    "system.log_sink_port",
                    f"日誌寫入器連線埠 {self.args.log_port} 已被占用，改用 {sink.port}",
                )
            return
        # 兩次都失敗時改為直接寫檔，至少保留紀錄
        os.environ.pop("AP_DRAWART_LOG_SINK", None)
        self.log.error("system.log_sink_failed", "無法啟動集中日誌寫入器，改由各程序直接寫檔")

    def _preflight(self) -> list[str]:
        """回傳致命問題（第一項為主要訊息）；非致命問題只寫日誌。"""
        paths = self.paths
        try:
            paths.ensure_dirs()
            probe = paths.tmp_dir / f"launcher-probe-{os.getpid()}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as error:
            self.log.error("system.preflight", f"資料目錄無法寫入：{error}", data_dir=str(paths.data_dir))
            return ["資料目錄無法寫入", str(paths.data_dir), str(error)]

        python = resolve_child_python(Path(sys.executable))
        if not python.is_file():
            self.log.error("system.preflight", "找不到後端執行環境", python=str(python))
            return ["找不到後端執行環境", str(python)]

        if not port_available(http_host(), self.port):
            self.log.error(
                "system.preflight",
                f"Web 連線埠 {self.port} 已被其他程式占用，未啟動展覽服務",
                port=self.port,
            )
            return [f"Web 連線埠 {self.port} 已被其他程式占用", "請關閉占用的程式，或改用其他連線埠後重新執行"]

        if not paths.base_image.is_file():
            self.log.warning("system.preflight", "找不到藝術家原圖 base.webp", path=str(paths.base_image))

        missing = [name for name in ("draw", "display", "admin") if not (paths.static_dir / name / "index.html").is_file()]
        if missing:
            self.log.error(
                "system.preflight",
                f"前端建置檔缺失：{', '.join(missing)}",
                static_dir=str(paths.static_dir),
            )

        if not self.args.no_displays:
            monitors = list_monitors()
            if len(monitors) < len(DISPLAY_ROLES):
                self.log.error(
                    "system.preflight",
                    f"偵測到 {len(monitors)} 個螢幕，少於展示所需的 3 個",
                    monitors=len(monitors),
                )
        self.log.info("system.preflight", "環境檢查完成", python=str(python), static_dir=str(paths.static_dir))
        return []

    def _start_children(self) -> None:
        python = str(resolve_child_python(Path(sys.executable)))
        env = child_env(dict(os.environ), os.environ.get("AP_DRAWART_LOG_SINK"), self.port, CODE_ROOT)
        has_console = console_attached()
        for name, module in (("web", "backend"), ("worker", "backend.worker")):
            spec = ChildSpec(name=name, args=[python, "-m", module], env=env, cwd=CODE_ROOT)
            child = ManagedChild(spec=spec, log=self.log, has_console=has_console)
            self._start_child(child)
            self.children.append(child)

    def _start_child(self, child: ManagedChild) -> None:
        try:
            child.start()
        except OSError as error:
            delay = child.on_exit(time.monotonic())
            self.log.error(
                "system.child_start_failed",
                f"{child.spec.name} 無法啟動：{error}，{delay:.0f} 秒後重試",
                child=child.spec.name,
            )
            return
        self.log.info("system.child_start", f"啟動子程序 {child.spec.name}", child=child.spec.name, pid=child.pid)

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.args.health_timeout
        while time.monotonic() < deadline:
            self._check_children(time.monotonic())
            health = self.health.fetch()
            if is_ready(health):
                self._on_ready(health)
                return
            time.sleep(0.5)
        self.log.error(
            "system.launcher_not_ready",
            f"Web 服務在 {self.args.health_timeout:.0f} 秒內未就緒，持續監看並重試",
            port=self.port,
        )
        self._show_status("展覽系統尚未就緒", "Web 服務沒有回應，系統會持續重試。", ["詳細原因請查看 logs/runtime.txt"])

    def _on_ready(self, health: dict[str, Any]) -> None:
        self.ready = True
        self.last_health = health
        # 啟動前遺留的要求不執行，避免一開展就被結束
        for name in pending_requests(health):
            self.health.clear_request(name)
            self.log.info("system.launcher_request_stale", f"清除啟動前遺留的要求：{name}", request=name)
        status = health.get("status")
        admin_urls = health.get("admin_urls") or []
        self.log.info(
            "system.launcher_ready",
            f"展覽服務就緒（狀態：{status}）",
            admin_urls=", ".join(admin_urls) or None,
        )
        if status == "setup_required":
            self.log.warning("system.setup_required", "尚未完成首次設定，請由同網路裝置開啟後臺設定 OpenAI 金鑰")
        self._open_live_displays(health)

    # ---- 監看 ----

    def _supervise(self) -> int:
        next_health = 0.0
        next_window_check = time.monotonic() + _WINDOW_CHECK_INTERVAL
        while self.stop_reason is None:
            now = time.monotonic()
            self._check_children(now)
            if now >= next_health:
                next_health = now + _HEALTH_INTERVAL
                self._poll_health()
            if now >= next_window_check:
                next_window_check = now + _WINDOW_CHECK_INTERVAL
                self._ensure_windows(now)
            time.sleep(0.5)
        return 0

    def _check_children(self, now: float) -> None:
        for child in self.children:
            if child.process is None:
                if child.restart_at is not None and now >= child.restart_at:
                    child.total_restarts += 1
                    self._start_child(child)
                continue
            code = child.poll()
            if code is None:
                continue
            if child.restart_at is None:
                delay = child.on_exit(now)
                self.log.error(
                    "system.child_exit",
                    f"子程序 {child.spec.name} 結束（代碼 {code}），{delay:.0f} 秒後重啟",
                    child=child.spec.name,
                    pid=child.pid,
                    exit_code=code,
                )
            elif now >= child.restart_at:
                child.total_restarts += 1
                self.log.warning(
                    "system.child_restart",
                    f"重啟子程序 {child.spec.name}（第 {child.total_restarts} 次）",
                    child=child.spec.name,
                )
                child.process = None
                self._start_child(child)

    def _poll_health(self) -> None:
        health = self.health.fetch()
        if health is None:
            return
        self.last_health = health
        if not self.ready and is_ready(health):
            self._on_ready(health)
            return
        if self.ready and self.display_mode == "status" and not frontend_missing(health):
            self._open_live_displays(health)
        for name in pending_requests(health):
            self._handle_request(name, health)
            if self.stop_reason is not None:
                return

    def _handle_request(self, name: str, health: dict[str, Any]) -> None:
        # 先清除再執行，避免下次啟動又被同一個要求影響
        cleared = self.health.clear_request(name)
        if name == "shutdown":
            self.log.info("system.launcher_request", "後臺要求結束展覽", request=name, cleared=cleared)
            self.stop_reason = "後臺要求結束展覽"
        elif name == "relaunch_displays":
            self.log.info("system.launcher_request", "後臺要求重新開啟展示視窗", request=name, cleared=cleared)
            self._open_live_displays(health, force=True)

    # ---- 展示視窗 ----

    def _display_targets(self) -> tuple[Path | None, dict[str, dict[str, Any] | None]]:
        config = read_displays_config()
        browser_path = browser.find_browser(str(config.get("browser") or "auto"), self.paths.root)
        if browser_path is None:
            self.log.error("system.display_browser", "找不到可用的展示瀏覽器", setting=config.get("browser"))
        assigned, warnings = browser.assign_monitors(config, list_monitors())
        for warning in warnings:
            self.log.warning("system.display_assign", warning)
        return browser_path, assigned

    def _launch_window(self, browser_path: Path, window: DisplayWindow) -> None:
        browser.kill_profile_processes(window.profile_dir)
        command = browser.browser_command(browser_path, window.url, window.profile_dir, window.monitor)
        try:
            browser.open_window(command)
        except OSError as error:
            self.log.error("system.display_window", f"無法開啟 {window.role} 展示視窗：{error}", role=window.role)
            return
        window.launched_at = time.monotonic()
        monitor = window.monitor
        self.log.info(
            "system.display_window_open",
            f"開啟 {window.role} 展示視窗",
            role=window.role,
            monitor=monitor["index"] if monitor else None,
            url=window.url,
        )

    def _open_windows(self, url_for_role: dict[str, str]) -> None:
        browser_path, assigned = self._display_targets()
        if browser_path is None:
            return
        profiles = self.paths.data_dir / "browser-profiles"
        for role in DISPLAY_ROLES:
            window = DisplayWindow(role, profiles / role, assigned.get(role), url_for_role[role])
            self.windows[role] = window
            self._launch_window(browser_path, window)

    def _open_live_displays(self, health: dict[str, Any], force: bool = False) -> None:
        if self.args.no_displays:
            return
        missing = frontend_missing(health)
        if missing:
            if self.display_mode != "status":
                self._show_status(
                    "展覽畫面無法載入",
                    "前端建置檔缺失，請重新安裝交付包。",
                    [f"缺少：{', '.join(missing)}"],
                    health.get("admin_urls") or [],
                )
            return
        if self.display_mode == "live" and not force:
            return
        self._open_windows({role: f"{self.base_url}/display/{role}" for role in DISPLAY_ROLES})
        self.display_mode = "live"

    def _show_status(
        self, title: str, message: str, details: list[str], admin_urls: list[str] | None = None
    ) -> None:
        url = write_status_page(
            self.paths.tmp_dir / "launcher-status.html", title, message, details, admin_urls or []
        )
        self.log.info("system.status_page", f"{title}：{message}", page=url)
        if self.args.no_displays:
            return
        self._open_windows({role: url for role in DISPLAY_ROLES})
        self.display_mode = "status"

    def _ensure_windows(self, now: float) -> None:
        """展示視窗被關閉時補開（僅限正式展示畫面）。"""
        if self.args.no_displays or self.display_mode != "live":
            return
        browser_path = None
        for window in self.windows.values():
            if now - window.launched_at < _WINDOW_LAUNCH_GRACE:
                continue
            if browser.find_profile_processes(window.profile_dir):
                continue
            if browser_path is None:
                browser_path, _ = self._display_targets()
                if browser_path is None:
                    return
            window.reopen_count += 1
            self.log.warning(
                "system.display_window_reopen",
                f"{window.role} 展示視窗已關閉，重新開啟（第 {window.reopen_count} 次）",
                role=window.role,
            )
            self._launch_window(browser_path, window)

    # ---- 收尾 ----

    def _shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        reason = self.stop_reason or "啟動器結束"
        if not (self.keep_status_windows and self.display_mode == "status"):
            for window in self.windows.values():
                try:
                    browser.kill_profile_processes(window.profile_dir)
                except Exception:  # noqa: BLE001 - 收尾時盡量繼續
                    pass
        # 先停 Worker，再停 Web
        for child in reversed(self.children):
            code = child.stop()
            self.log.info(
                "system.child_stop",
                f"停止子程序 {child.spec.name}（代碼 {code}）",
                child=child.spec.name,
                pid=child.pid,
            )
        self.log.info("system.launcher_stop", f"啟動器結束：{reason}")
        if self.sink is not None:
            _flush_log_client()
            self.sink.stop()
        self.instance.release()


def _flush_log_client(timeout: float = 2.0) -> None:
    """把本程序尚未送出的日誌送達寫入器，再關閉寫入器。"""
    from backend import logclient

    logclient.close(timeout)


def main(argv: list[str] | None = None) -> int:
    return Launcher(parse_args(argv)).run()
