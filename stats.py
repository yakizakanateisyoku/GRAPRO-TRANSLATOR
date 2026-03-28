"""
stats.py - LCT 稼働統計モジュール
翻訳イベントとシステムスナップショットをSQLiteに記録する
"""
import sqlite3
import threading
import time
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "logs", "lct_stats.db")
_lock = threading.Lock()
_conn = None

def _get_conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                port            INTEGER NOT NULL,
                source_lang     TEXT,
                success         INTEGER NOT NULL DEFAULT 1,
                response_ms     REAL,
                chars_in        INTEGER,
                chars_out       INTEGER,
                error_msg       TEXT
            )
        """)
        # 既存DBへのマイグレーション（カラムがなければ追加）
        # 旧バージョンのテキストカラムを削除（プライバシー対応）
        existing = [r[1] for r in _conn.execute("PRAGMA table_info(translations)").fetchall()]
        if "original_text" in existing or "translated_text" in existing:
            _conn.execute("UPDATE translations SET original_text=NULL, translated_text=NULL WHERE original_text IS NOT NULL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS system_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                port          INTEGER NOT NULL,
                cpu_percent   REAL,
                memory_mb     REAL,
                queue_size    INTEGER,
                message_count INTEGER
            )
        """)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback_reports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                source_lang   TEXT,
                target_lang   TEXT DEFAULT 'ja'
            )
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_trans_ts ON translations(timestamp)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts  ON system_snapshots(timestamp)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_ts    ON feedback_reports(timestamp)")
        _conn.commit()
    return _conn

def record_translation(port, source_lang, success, response_ms,
                       chars_in=0, chars_out=0, error_msg=None,
                       original_text=None, translated_text=None):
    """翻訳1件を記録する（テキストは保存しない）"""
    try:
        with _lock:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO translations
                    (timestamp, port, source_lang, success, response_ms,
                     chars_in, chars_out, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(timespec="seconds"),
                port, source_lang,
                1 if success else 0,
                response_ms, chars_in, chars_out, error_msg
            ))
            conn.commit()
    except Exception as e:
        print(f"[stats] record_translation error: {e}")

def record_feedback(source_lang, target_lang="ja"):
    """翻訳品質フィードバックを記録（テキストは保存しない）"""
    try:
        with _lock:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO feedback_reports (timestamp, source_lang, target_lang)
                VALUES (?, ?, ?)
            """, (datetime.now().isoformat(timespec="seconds"), source_lang, target_lang))
            conn.commit()
    except Exception as e:
        print(f"[stats] record_feedback error: {e}")

def record_snapshot(port, queue_size, message_count):
    """システムスナップショットを記録する（30秒ごと呼び出し）"""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        cpu = proc.cpu_percent(interval=0.1)
        mem = proc.memory_info().rss / 1024 / 1024
    except ImportError:
        cpu = None
        mem = None
    try:
        with _lock:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO system_snapshots
                    (timestamp, port, cpu_percent, memory_mb, queue_size, message_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(timespec="seconds"),
                port, cpu, mem, queue_size, message_count
            ))
            conn.commit()
    except Exception as e:
        print(f"[stats] record_snapshot error: {e}")

def get_summary(port=None, hours=1):
    """直近N時間のサマリーを返す"""
    try:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        conn = _get_conn()
        where_port = f"AND port = {port}" if port else ""

        # 翻訳集計
        row = conn.execute(f"""
            SELECT
                COUNT(*)                                AS total,
                SUM(success)                            AS succeeded,
                COUNT(*) - SUM(success)                 AS failed,
                ROUND(AVG(response_ms), 1)              AS avg_ms,
                ROUND(MIN(response_ms), 1)              AS min_ms,
                ROUND(MAX(response_ms), 1)              AS max_ms,
                SUM(chars_in)                           AS total_chars
            FROM translations
            WHERE timestamp >= '{since}' {where_port}
        """).fetchone()

        # 言語内訳
        langs = conn.execute(f"""
            SELECT source_lang, COUNT(*) AS cnt
            FROM translations
            WHERE timestamp >= '{since}' {where_port}
            GROUP BY source_lang
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()

        # システム平均
        sys_row = conn.execute(f"""
            SELECT
                ROUND(AVG(cpu_percent), 1)  AS avg_cpu,
                ROUND(AVG(memory_mb), 1)    AS avg_mem,
                ROUND(MAX(memory_mb), 1)    AS peak_mem
            FROM system_snapshots
            WHERE timestamp >= '{since}' {where_port}
        """).fetchone()

        return {
            "period_hours": hours,
            "port": port or "all",
            "translations": {
                "total":      row[0],
                "succeeded":  row[1],
                "failed":     row[2],
                "avg_ms":     row[3],
                "min_ms":     row[4],
                "max_ms":     row[5],
                "total_chars": row[6],
            },
            "languages": [{"lang": r[0], "count": r[1]} for r in langs],
            "system": {
                "avg_cpu_percent": sys_row[0],
                "avg_memory_mb":   sys_row[1],
                "peak_memory_mb":  sys_row[2],
            }
        }
    except Exception as e:
        return {"error": str(e)}

def _checkpoint_loop(interval=300):
    """5分ごとにWAL checkpointを実行してDBファイルを肥大化させない"""
    while True:
        time.sleep(interval)
        try:
            with _lock:
                _get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            print(f"[stats] checkpoint error: {e}")

def start_snapshot_thread(port, get_queue_size, get_message_count, interval=30):
    """バックグラウンドでスナップショットを定期記録するスレッドを開始"""
    def _loop():
        while True:
            try:
                record_snapshot(port, get_queue_size(), get_message_count())
            except Exception as e:
                print(f"[stats] snapshot error: {e}")
            time.sleep(interval)
    t = threading.Thread(target=_loop, daemon=True, name=f"stats-snapshot-{port}")
    t.start()
    threading.Thread(target=_checkpoint_loop, daemon=True, name='stats-wal-cp').start()
    return t
