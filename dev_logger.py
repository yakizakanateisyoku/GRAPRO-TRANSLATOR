"""
dev_logger.py - GRAPRO-TRANSLATOR 開発者モード ロガー
v1.0 (2026/05/18 01:30 JST)

目的:
  「起動して長時間運用していると突然落ちる」原因を取りこぼさず捕捉する。
  pythonw(GUI)起動では stdout/stderr が消えるため、ファイル保存が必須。

特徴:
  - 開発者モードがON のときだけ有効化（OFFなら何もしない / 既存print挙動のまま）
  - exe隣の logs/ に grapro_YYYYMMDD_HHMMSS.log を作成
  - RotatingFileHandler (20MB×5バックアップ) で長時間運用でも肥大化しない
  - sys.excepthook / threading.excepthook をフックして未捕捉例外を確実に記録
  - faulthandler を有効化（Cレベルのセグフォルト・スタック破壊も補足）
  - print() を logger 経由に redirect（既存コードはそのまま、ログにも出る）
  - 5分ごとに heartbeat（uptime / 起動中スレッド数）を記録 → デッドロック/リーク発見の手がかり
  - ランタイム enable() / disable() で再起動なしに切替可能（設定はconfig.json）

依存:
  標準ライブラリのみ。psutil 等の追加依存なし。
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
import threading
import traceback
import faulthandler
from datetime import datetime
from logging.handlers import RotatingFileHandler

_LOGGER_NAME = "grapro"
_STATE_LOCK = threading.RLock()

_enabled: bool = False
_session_id: str | None = None
_log_dir: str | None = None
_current_log_file: str | None = None
_logger: logging.Logger | None = None
_file_handler: logging.Handler | None = None
_stream_handler: logging.Handler | None = None
_faulthandler_file = None

# 元のフック / ストリームを保管しておき、disable() で復元する
_orig_excepthook = None
_orig_thread_excepthook = None
_orig_stdout = None
_orig_stderr = None
_heartbeat_stop: threading.Event | None = None


def get_log_dir() -> str:
    """exe(または__file__)の隣の logs/ ディレクトリパスを返す（作成はしない）"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "logs")


def get_current_log_file() -> str | None:
    """有効中のログファイル絶対パス（無効時 None）"""
    return _current_log_file


def is_enabled() -> bool:
    return _enabled


def get_logger() -> logging.Logger:
    """常に取れる logger。dev modeでも非dev modeでも安全に使える"""
    return logging.getLogger(_LOGGER_NAME)


class _StreamToLogger(io.TextIOBase):
    """既存の print() を logger 経由に流すラッパー。元ストリームにも書き出す"""
    def __init__(self, level: int, original):
        super().__init__()
        self._level = level
        self._original = original
        self._buf = ""

    def write(self, msg):
        try:
            if self._original is not None:
                self._original.write(msg)
        except Exception:
            pass
        if not isinstance(msg, str):
            return len(msg) if msg else 0
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                _logger.log(self._level, "[%s] %s",
                            "stdout" if self._level == logging.INFO else "stderr",
                            line.rstrip())
        return len(msg)

    def flush(self):
        try:
            if self._original is not None:
                self._original.flush()
        except Exception:
            pass
        if self._buf.strip():
            _logger.log(self._level, "[flush] %s", self._buf.rstrip())
            self._buf = ""

    def isatty(self):
        try:
            return self._original.isatty() if self._original else False
        except Exception:
            return False


def _start_heartbeat():
    """5分ごとに簡易ヘルス情報をログ。長時間運用でリーク/デッドロックの目印に"""
    global _heartbeat_stop
    _heartbeat_stop = threading.Event()
    start = time.monotonic()

    def _loop():
        while not _heartbeat_stop.is_set():
            try:
                uptime_s = int(time.monotonic() - start)
                threads = threading.active_count()
                thread_names = sorted({t.name for t in threading.enumerate()})
                _logger.info("[heartbeat] uptime=%ds threads=%d names=%s",
                             uptime_s, threads, ",".join(thread_names))
            except Exception as e:
                try:
                    _logger.warning("[heartbeat] failed: %r", e)
                except Exception:
                    pass
            _heartbeat_stop.wait(300)  # 5分

    threading.Thread(target=_loop, name="dev-heartbeat", daemon=True).start()


def enable() -> str | None:
    """開発者モードを有効化。ログファイルのパスを返す（既に有効なら現行ファイルを返す）"""
    global _enabled, _session_id, _log_dir, _current_log_file, _logger
    global _file_handler, _stream_handler, _faulthandler_file
    global _orig_excepthook, _orig_thread_excepthook, _orig_stdout, _orig_stderr

    with _STATE_LOCK:
        if _enabled:
            return _current_log_file

        _log_dir = get_log_dir()
        try:
            os.makedirs(_log_dir, exist_ok=True)
        except Exception as e:
            # フォールバック: %TEMP% に書く
            import tempfile
            _log_dir = os.path.join(tempfile.gettempdir(), "GraproTranslator", "logs")
            os.makedirs(_log_dir, exist_ok=True)

        _session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        _current_log_file = os.path.join(_log_dir, f"grapro_{_session_id}.log")

        _logger = logging.getLogger(_LOGGER_NAME)
        _logger.setLevel(logging.DEBUG)
        # 既存ハンドラがあれば撤去（再enable対策）
        for h in list(_logger.handlers):
            _logger.removeHandler(h)

        fmt = logging.Formatter(
            "[%(asctime)s.%(msecs)03d][%(levelname)-5s][%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # ファイル: 20MB×5 ローテーション
        _file_handler = RotatingFileHandler(
            _current_log_file, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(fmt)
        _logger.addHandler(_file_handler)

        # CLI起動時（stderr あり）はコンソールにも出す
        try:
            if sys.stderr is not None and hasattr(sys.stderr, "write"):
                _stream_handler = logging.StreamHandler(sys.stderr)
                _stream_handler.setLevel(logging.INFO)
                _stream_handler.setFormatter(fmt)
                _logger.addHandler(_stream_handler)
        except Exception:
            _stream_handler = None

        # 未捕捉例外フック
        _orig_excepthook = sys.excepthook

        def _excepthook(exc_type, exc_value, exc_tb):
            try:
                tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                _logger.critical("UNHANDLED EXCEPTION (main thread)\n%s", tb)
                if _file_handler:
                    _file_handler.flush()
            finally:
                # オリジナルに譲渡（デバッガ等の動作を壊さない）
                try:
                    if _orig_excepthook:
                        _orig_excepthook(exc_type, exc_value, exc_tb)
                except Exception:
                    pass

        sys.excepthook = _excepthook

        # スレッド未捕捉例外（Python 3.8+）
        _orig_thread_excepthook = getattr(threading, "excepthook", None)

        def _thread_excepthook(args):
            try:
                tname = args.thread.name if args.thread else "?"
                tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
                _logger.critical("UNHANDLED THREAD EXCEPTION in %s\n%s", tname, tb)
                if _file_handler:
                    _file_handler.flush()
            finally:
                try:
                    if _orig_thread_excepthook:
                        _orig_thread_excepthook(args)
                except Exception:
                    pass

        try:
            threading.excepthook = _thread_excepthook
        except Exception:
            pass

        # faulthandler（Cレベルクラッシュ）
        try:
            fault_path = os.path.join(_log_dir, f"grapro_{_session_id}_fault.log")
            _faulthandler_file = open(fault_path, "w", encoding="utf-8")
            faulthandler.enable(file=_faulthandler_file, all_threads=True)
        except Exception as e:
            try:
                _logger.warning("faulthandler enable failed: %r", e)
            except Exception:
                pass

        # stdout/stderr を logger に redirect（元の出力先も維持）
        _orig_stdout = sys.stdout
        _orig_stderr = sys.stderr
        try:
            sys.stdout = _StreamToLogger(logging.INFO, _orig_stdout)
        except Exception:
            sys.stdout = _orig_stdout
        try:
            sys.stderr = _StreamToLogger(logging.ERROR, _orig_stderr)
        except Exception:
            sys.stderr = _orig_stderr

        # セッション開始バナー
        _logger.info("=" * 70)
        _logger.info("GRAPRO-TRANSLATOR Developer Mode logging START  session=%s", _session_id)
        _logger.info("  python      = %s", sys.version.replace("\n", " "))
        _logger.info("  frozen      = %s", getattr(sys, "frozen", False))
        _logger.info("  executable  = %s", sys.executable)
        _logger.info("  cwd         = %s", os.getcwd())
        _logger.info("  log_dir     = %s", _log_dir)
        _logger.info("  log_file    = %s", _current_log_file)
        _logger.info("  pid         = %d", os.getpid())
        _logger.info("=" * 70)

        _start_heartbeat()

        _enabled = True
        return _current_log_file


def disable() -> None:
    """開発者モードを無効化。フック・ストリームを元に戻す"""
    global _enabled, _logger, _file_handler, _stream_handler, _faulthandler_file
    global _orig_excepthook, _orig_thread_excepthook, _orig_stdout, _orig_stderr
    global _heartbeat_stop, _current_log_file

    with _STATE_LOCK:
        if not _enabled:
            return

        try:
            if _logger:
                _logger.info("GRAPRO-TRANSLATOR Developer Mode logging STOP")
        except Exception:
            pass

        if _heartbeat_stop is not None:
            try:
                _heartbeat_stop.set()
            except Exception:
                pass
            _heartbeat_stop = None

        try:
            if _orig_excepthook is not None:
                sys.excepthook = _orig_excepthook
        except Exception:
            pass

        try:
            if _orig_thread_excepthook is not None:
                threading.excepthook = _orig_thread_excepthook
        except Exception:
            pass

        try:
            faulthandler.disable()
        except Exception:
            pass

        try:
            if _faulthandler_file is not None:
                _faulthandler_file.close()
        except Exception:
            pass
        _faulthandler_file = None

        try:
            sys.stdout = _orig_stdout if _orig_stdout is not None else sys.__stdout__
        except Exception:
            pass
        try:
            sys.stderr = _orig_stderr if _orig_stderr is not None else sys.__stderr__
        except Exception:
            pass

        if _logger:
            for h in list(_logger.handlers):
                try:
                    h.flush()
                    h.close()
                except Exception:
                    pass
                _logger.removeHandler(h)

        _file_handler = None
        _stream_handler = None
        _orig_excepthook = None
        _orig_thread_excepthook = None
        _orig_stdout = None
        _orig_stderr = None
        _enabled = False
        # _current_log_file は最後の場所を覚えておく（「ログ開く」UIで使う）


def open_log_folder() -> bool:
    """OS既定のファイラで logs/ を開く。enable中でなくても可"""
    target = _log_dir or get_log_dir()
    try:
        if not os.path.isdir(target):
            os.makedirs(target, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess; subprocess.Popen(["open", target])
        else:
            import subprocess; subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False
