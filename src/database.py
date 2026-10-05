"""Bước 4: SQLite log lịch sử video + số liệu hiệu suất.

Không có API YouTube Analytics / Shopee Affiliate tự động (cần OAuth + đăng ký
app riêng, ngoài phạm vi 1 tool cá nhân) — số liệu hiệu suất (view, CTR, click
affiliate, hoa hồng) do người dùng TỰ NHẬP sau khi đăng video và theo dõi vài
ngày, qua `python main.py --log-performance`. Việc log lịch sử (ngách, sản
phẩm, đường dẫn video) thì tự động mỗi khi ghép video xong.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path("data/history.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    niche TEXT,
    video_title TEXT,
    video_path TEXT,
    product_names TEXT,      -- JSON list
    youtube_url TEXT,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS performance (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    views INTEGER,
    ctr REAL,                 -- % click-through thumbnail
    watch_time_seconds INTEGER,
    affiliate_clicks INTEGER,
    revenue REAL,              -- hoa hồng thực tế (VND)
    updated_at TEXT
);
"""


def _connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_run(
    run_id: str,
    niche: Optional[str],
    video_title: Optional[str],
    video_path: Optional[str],
    product_names: List[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Ghi/cập nhật 1 lần chạy vào bảng runs — gọi tự động sau khi ghép video
    xong (main.py). An toàn để gọi lại nhiều lần cho cùng run_id (upsert)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO runs (run_id, created_at, niche, video_title, video_path, product_names)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                niche=excluded.niche,
                video_title=excluded.video_title,
                video_path=excluded.video_path,
                product_names=excluded.product_names
            """,
            (run_id, _now(), niche, video_title, video_path, json.dumps(product_names, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def update_performance(
    run_id: str,
    db_path: Path = DEFAULT_DB_PATH,
    views: Optional[int] = None,
    ctr: Optional[float] = None,
    watch_time_seconds: Optional[int] = None,
    affiliate_clicks: Optional[int] = None,
    revenue: Optional[float] = None,
    youtube_url: Optional[str] = None,
) -> bool:
    """Cập nhật số liệu hiệu suất do người dùng tự nhập — chỉ set những field
    được truyền vào (khác None), giữ nguyên field cũ nếu không truyền lại.
    Trả về False nếu run_id chưa từng được log_run()."""
    conn = _connect(db_path)
    try:
        exists = conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not exists:
            return False

        if youtube_url is not None:
            conn.execute(
                "UPDATE runs SET youtube_url = ?, published_at = COALESCE(published_at, ?) WHERE run_id = ?",
                (youtube_url, _now(), run_id),
            )

        current = conn.execute("SELECT * FROM performance WHERE run_id = ?", (run_id,)).fetchone()
        merged = {
            "views": views if views is not None else (current["views"] if current else None),
            "ctr": ctr if ctr is not None else (current["ctr"] if current else None),
            "watch_time_seconds": watch_time_seconds if watch_time_seconds is not None else (current["watch_time_seconds"] if current else None),
            "affiliate_clicks": affiliate_clicks if affiliate_clicks is not None else (current["affiliate_clicks"] if current else None),
            "revenue": revenue if revenue is not None else (current["revenue"] if current else None),
        }
        conn.execute(
            """
            INSERT INTO performance (run_id, views, ctr, watch_time_seconds, affiliate_clicks, revenue, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                views=excluded.views, ctr=excluded.ctr, watch_time_seconds=excluded.watch_time_seconds,
                affiliate_clicks=excluded.affiliate_clicks, revenue=excluded.revenue, updated_at=excluded.updated_at
            """,
            (run_id, merged["views"], merged["ctr"], merged["watch_time_seconds"],
             merged["affiliate_clicks"], merged["revenue"], _now()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_runs(db_path: Path = DEFAULT_DB_PATH) -> List[dict]:
    """Toàn bộ lịch sử, kèm hiệu suất nếu đã nhập — sắp xếp mới nhất trước."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT r.run_id, r.created_at, r.niche, r.video_title, r.video_path,
                   r.product_names, r.youtube_url, r.published_at,
                   p.views, p.ctr, p.watch_time_seconds, p.affiliate_clicks, p.revenue
            FROM runs r LEFT JOIN performance p ON p.run_id = r.run_id
            ORDER BY r.created_at DESC
            """
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["product_names"] = json.loads(d["product_names"]) if d["product_names"] else []
            result.append(d)
        return result
    finally:
        conn.close()
