"""Entry point.

Run from the project root:
    .venv\\Scripts\\python -m app.main             # participant window fullscreen on the tracked monitor
    .venv\\Scripts\\python -m app.main --windowed  # participant window in a resizable window (testing)
    .venv\\Scripts\\python -m app.main --check     # validate config, content and media, then exit

The Workspace opens first (on a monitor other than the tracked one, if available). Live ->
"Start session" opens the participant window with the intake form. "End session" (or Ctrl+Shift+Q
in the participant window) ends it; the next participant can start right away. Recordings,
Enrichments, Visualizations and Downloads are in the Workspace sidebar. Closing it quits.
Configuration: config.yaml next to the app, or the file named in GAZE_APP_CONFIG.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from typing import Optional

# Silence Qt's FFmpeg backend, which prints stream info for every video it opens.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from app import i18n  # noqa: E402
from app.config import AppConfig, ConfigError, load_config  # noqa: E402
from app.core.repository import ContentRepository  # noqa: E402
from app.camera.source import CameraSource  # noqa: E402
from app.gaze.gazepoint_client import GazepointClient  # noqa: E402
from app.gaze.mock_server import MockGazeServer  # noqa: E402
from app.gaze.mouse_source import MouseSource  # noqa: E402
from app.gaze.source import GazeSource  # noqa: E402
from app.llm.factory import make_llm_client  # noqa: E402
from app.loop.controller import SessionController, State  # noqa: E402
from app.pipeline.archive import participant_exists  # noqa: E402
from app.ui.participant_window import ParticipantWindow  # noqa: E402
from app.ui.workspace import Workspace  # noqa: E402
from app.ui.setup_screen import SessionConfigBox  # noqa: E402
from app.ui.style import FONT_NAME  # noqa: E402
from app.ui.theme import apply_light_theme  # noqa: E402

log = logging.getLogger("app")


def setup_logging(cfg: AppConfig) -> None:
    """Rotating log file in paths.logs plus stderr. Never logs API keys (they are never read here)."""
    logs = cfg.resolve(cfg.paths.logs)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        logs.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            logs / "app.log", maxBytes=cfg.logging.max_kb * 1024, backupCount=cfg.logging.backups, encoding="utf-8"))
    except OSError as exc:
        print(f"WARNING: cannot write log files to {logs}: {exc}", file=sys.stderr)
    logging.basicConfig(level=getattr(logging, cfg.logging.level), handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def install_exception_hooks() -> None:
    """Log uncaught exceptions (GUI thread and worker threads) instead of silently dying."""
    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        app = QApplication.instance()
        if app is not None and threading.current_thread() is threading.main_thread():
            QMessageBox.critical(None, "Unexpected error",
                                 f"{exc_type.__name__}: {exc}\n\nThe details were written to the log file.")

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


def check(cfg: AppConfig, repo: ContentRepository) -> int:
    ok = True
    print(f"Config OK. AOIs: {', '.join(cfg.aoi)}")
    entries = repo.list_sets()
    if not entries:
        print("No instruction sets found.")
        ok = False
    for e in entries:
        title = e.iset.title if e.iset else "(unreadable)"
        print(f"{e.path.name}: '{title}' -> {'OK' if e.ok else 'PROBLEMS'}")
        for err in e.errors:
            print(f"    {err}")
        ok &= e.ok
    catalog = repo.catalog()
    missing = [i.path for i in catalog.items if repo.media_file(i.path) is None]
    print(f"Media catalog: {len(catalog.items)} entries, missing files: {missing or 'none'}")
    rules = repo.rules()
    print(f"Rules: {sum(r.enabled for r in rules.rules)} enabled of {len(rules.rules)}")
    if cfg.post_survey.enabled:
        print(f"Post survey '{cfg.post_survey.survey}': {'found' if repo.survey(cfg.post_survey.survey) else 'MISSING'}")
    print(f"{cfg.llm.gemini.api_key_env}: {'set' if os.environ.get(cfg.llm.gemini.api_key_env) else 'not set'}")
    return 0 if ok else 1


class SourceFactory:
    """Creates gaze sources for the setup box. 'mock' runs the fake GP3 server in-process."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.mock_server: Optional[MockGazeServer] = None

    def __call__(self, name: str) -> GazeSource:
        g = self.cfg.gaze
        if name == "mouse":
            return MouseSource(g.mouse.rate_hz, self.cfg.screen.monitor_index)
        if name == "gazepoint":
            return GazepointClient(g.gazepoint)
        if name == "mock":
            if self.mock_server is None:
                try:
                    self.mock_server = MockGazeServer(g.mock_server.host, g.mock_server.port,
                                                      g.mock_server.rate_hz, self.cfg).start()
                except OSError:
                    pass  # port taken: assume an external `python -m app.gaze.mock_server` is running
            return GazepointClient(g.gazepoint, host=g.mock_server.host, port=g.mock_server.port)
        raise ValueError(f"unknown gaze source {name!r}")

    def shutdown(self) -> None:
        if self.mock_server is not None:
            self.mock_server.shutdown()
            self.mock_server = None


def _screen(cfg: AppConfig):
    screens = QGuiApplication.screens()
    idx = cfg.screen.monitor_index
    return screens[idx] if idx < len(screens) else QGuiApplication.primaryScreen()


def show_participant_window(win: QWidget, cfg: AppConfig, windowed: bool) -> None:
    screen = _screen(cfg)
    win.setScreen(screen)
    if windowed or not cfg.screen.fullscreen:
        geo = screen.availableGeometry()
        win.resize(min(1280, geo.width()), min(720, geo.height()))
        win.move(geo.center() - win.rect().center())
        win.show()
    else:
        win.setGeometry(screen.geometry())
        win.showFullScreen()
    win.raise_()
    win.activateWindow()


def show_panel(panel: QWidget, cfg: AppConfig) -> None:
    """Put the cockpit on a monitor other than the participant's, if there is one."""
    tracked = _screen(cfg)
    others = [s for s in QGuiApplication.screens() if s is not tracked]
    if others:
        geo = others[0].availableGeometry()
        panel.setScreen(others[0])
        panel.move(geo.topLeft())
        panel.resize(min(1440, geo.width()), min(920, geo.height()))
    panel.show()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="app.main")
    ap.add_argument("--windowed", action="store_true", help="show the participant window in a window instead of fullscreen")
    ap.add_argument("--check", action="store_true", help="validate config, content and media, then exit")
    args = ap.parse_args(argv[1:])

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg)
    i18n.configure(cfg.resolve(cfg.paths.locale), cfg.app.locale)
    repo = ContentRepository(cfg)
    if args.check:
        return check(cfg, repo)

    app = QApplication(argv)
    app.setApplicationName(cfg.app.name)
    app.setApplicationVersion(cfg.app.version)
    install_exception_hooks()
    apply_light_theme(app)              # participant screens are designed light, whatever the OS setting
    app.setFont(QFont(FONT_NAME, 10))
    if cfg.screen.monitor_index >= len(QGuiApplication.screens()):
        log.warning("monitor_index %s not found, using the primary screen", cfg.screen.monitor_index)

    sessions_root = cfg.resolve(cfg.paths.sessions)
    sources = SourceFactory(cfg)
    config_box = SessionConfigBox(sources, cfg.camera)
    workspace = Workspace(cfg, repo, config_box=config_box, preview_camera=lambda: config_box.camera)
    session: dict[str, object] = {}   # current "controller" and "window"

    def on_start(config: dict, source: GazeSource, camera: Optional[CameraSource]) -> None:
        old = session.get("window")
        if isinstance(old, ParticipantWindow):
            old.close()
            old.deleteLater()
        sets = repo.usable_sets()
        window = ParticipantWindow(cfg, sets, lambda pid: participant_exists(sessions_root, pid),
                                   repo.media_dir)
        controller = SessionController(cfg, window, lambda name: make_llm_client(name, cfg), repo)
        try:
            # e.g. a missing API key fails here, before the participant sees anything
            controller.begin(config, source, camera)
        except Exception as exc:  # noqa: BLE001 - shown to the experimenter
            log.exception("session start failed")
            source.stop()
            source.deleteLater()
            if camera is not None:
                camera.stop()
                camera.deleteLater()
            window.deleteLater()
            config_box.set_session_active(False)
            QMessageBox.critical(workspace, i18n.t("cockpit.start_failed"), str(exc))
            return
        if isinstance(source, MouseSource):
            source.set_reference_widget(window)
        session.update(controller=controller, window=window)
        controller.ended.connect(lambda: on_ended(controller, window))
        window.closed.connect(controller.force_end)
        config_box.set_session_active(True)
        workspace.attach(controller)
        show_participant_window(window, cfg, args.windowed)

    def on_ended(controller: SessionController, window: ParticipantWindow) -> None:
        if controller.logger is not None:
            log.info("session saved to %s", controller.logger.dir)
        if window.currentWidget() is not window.complete:
            window.hide()   # the "thank you" page stays visible until the next session starts
        # the session camera stays busy until open checks finish; give the preview its own later
        QTimer.singleShot(int(cfg.verification.drain_timeout_s * 1000) + 500
                          if controller.checks_pending else 0, lambda: config_box.set_session_active(False))

    def on_quit() -> None:
        ctl = session.get("controller")
        if isinstance(ctl, SessionController):
            if ctl.state != State.ENDED:
                ctl.force_end()
            ctl.wait_for_tasks(int(max(cfg.llm.gemini.timeout_s, cfg.llm.ollama.timeout_s) * 1000) + 5000)
        win = session.get("window")
        if isinstance(win, ParticipantWindow):
            win.close()
        config_box.stop_source()
        config_box.stop_camera()
        sources.shutdown()

    config_box.start_requested.connect(on_start)
    app.aboutToQuit.connect(on_quit)
    app.setQuitOnLastWindowClosed(False)
    workspace.destroyed.connect(app.quit)
    workspace.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    show_panel(workspace, cfg)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
