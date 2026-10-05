"""Chạy assemble_video ở THREAD NỀN, tách khỏi phiên Streamlit.

Lý do: ghép video có thể mất 30-40 phút. Nếu chạy thẳng trong script
Streamlit, chỉ cần tab trình duyệt ngủ/F5/bấm nút khác là Streamlit hủy phần
còn lại của lần chạy -> file MP4 vẫn ra nhưng không log DB, UI không báo xong.

Ở đây thread nền chạy độc lập với phiên, ghi trạng thái ra
<run_dir>/_render_status.json (state/phase/progress/error) để UI đọc lại bất
cứ lúc nào, kể cả sau khi tải lại trang. Registry thread là biến cấp module —
module chỉ import 1 lần/tiến trình nên sống qua mọi lần rerun của Streamlit.
"""
import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .video_assembly import assemble_video

STATUS_FILENAME = "_render_status.json"

_lock = threading.Lock()
_threads: dict = {}  # {str(run_dir): Thread}


def _status_path(run_dir: Path) -> Path:
    return run_dir / STATUS_FILENAME


def _write_status(run_dir: Path, **fields) -> None:
    path = _status_path(run_dir)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data.update(fields)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _is_alive(run_dir: Path) -> bool:
    t = _threads.get(str(run_dir))
    return bool(t and t.is_alive())


def any_render_running() -> Optional[str]:
    """Trả về tên run đang render (nếu có) — chỉ cho render 1 video/lần vì
    ghép video ăn gần hết CPU, chạy song song chỉ chậm cả hai."""
    with _lock:
        for key, t in _threads.items():
            if t.is_alive():
                return Path(key).name
    return None


def get_status(run_dir: Path) -> Optional[dict]:
    path = _status_path(run_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # File ghi "running" nhưng không còn thread nào -> server Streamlit đã bị
    # tắt/khởi động lại giữa chừng, render đã chết.
    if data.get("state") == "running" and not _is_alive(run_dir):
        data["state"] = "interrupted"
    return data


def start_render(
    run_dir: Path,
    canvas: tuple,
    font_path: Optional[str],
    fps: int,
    on_done: Optional[Callable[[Path, Path], None]] = None,
) -> bool:
    """Khởi động render nền. Trả về False nếu đang có render khác chạy."""
    with _lock:
        if any(t.is_alive() for t in _threads.values()):
            return False
        _write_status(
            run_dir,
            state="running", phase="Đang dựng các đoạn clip", progress=0.0,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None, error=None, canvas=list(canvas), fps=fps,
        )
        t = threading.Thread(
            target=_worker, args=(run_dir, canvas, font_path, fps, on_done),
            name=f"render-{run_dir.name}", daemon=True,
        )
        _threads[str(run_dir)] = t
        t.start()
        return True


def _worker(run_dir: Path, canvas: tuple, font_path: Optional[str], fps: int, on_done) -> None:
    last_write = [0.0]

    def on_progress(phase: str, fraction: float) -> None:
        now = time.monotonic()
        if now - last_write[0] < 1.0 and fraction < 1.0:
            return  # giới hạn ghi file ~1 lần/giây
        last_write[0] = now
        _write_status(run_dir, phase=phase, progress=round(fraction, 4))

    try:
        out_path = assemble_video(run_dir, canvas=canvas, font_path=font_path, fps=fps, progress_cb=on_progress)
        if on_done:
            try:
                on_done(run_dir, out_path)
            except Exception as e:  # lỗi log DB không được làm hỏng kết quả video
                print(f"[CẢNH BÁO] Ghép xong nhưng lưu lịch sử lỗi: {e}")
        _write_status(
            run_dir, state="done", phase="Hoàn tất", progress=1.0,
            finished_at=datetime.now().isoformat(timespec="seconds"), output=str(out_path),
        )
    except Exception as e:
        traceback.print_exc()
        _write_status(
            run_dir, state="error", error=f"{type(e).__name__}: {e}",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
