"""Làm đẹp draft CapCut vừa xuất bằng Auto Keyframe Pro (add-on của Tool
AutoCapcut V6.3, chép nguyên vào vendor/AutoKeyframePro — chỉ dùng thư viện
chuẩn, sửa thẳng draft_content.json).

Chạy SAU export_capcut_draft, trên chính thư mục draft đó:
- keyframe có ease in/out (Ken Burns Pro...) thay cho zoom đều của mình
- lớp nền mờ phủ kín khung + Canvas Blur gốc của CapCut
- clip video dọc để vừa khung, zoom rất nhẹ
- tuỳ chọn: phụ đề TỪ KHOÁ (chỉ thay track phụ đề, giữ badge TOP/giá/card),
  letterbox 19:6, vignette

AKP tự backup draft_content.json và ghi akp_state.json nên chạy lại nhiều lần
không bị chồng lớp. CapCut phải đóng khi chạy (CapCut ghi đè khi thoát).
"""
import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

_AKP_DIR = Path(__file__).resolve().parent.parent / "vendor" / "AutoKeyframePro"
if str(_AKP_DIR) not in sys.path:
    sys.path.insert(0, str(_AKP_DIR))

import akp_engine  # noqa: E402
import akp_subtitle  # noqa: E402

KEYFRAME_STYLES = [s for s in akp_engine.KEYFRAME_STYLES if s != "Không Sử Dụng"]
BLUR_LEVELS = list(akp_engine.CANVAS_BLUR_LEVELS)
SUBTITLE_STYLES = list(akp_subtitle.STYLE_PRESETS)

DEFAULT_OPTIONS = {
    **akp_engine.PRO_DEFAULTS,
    "keyword_subs": False,
    "sub_style": SUBTITLE_STYLES[0],
    "sub_max_words": 3,
    "sub_upper": False,
    "letterbox": False,
    "vignette": False,
    "vignette_alpha": 0.45,
}


def _subtitle_track(draft) -> Optional[dict]:
    """Track phụ đề của export_capcut_draft = track chữ nhiều đoạn nhất
    (mỗi câu ≥ 1 đoạn; badge/highlight/card thưa hơn nhiều)."""
    tracks = [t for t in draft.data.get("tracks", []) if t.get("type") == "text"
              and not any(s.get("desc") in akp_engine.AKP_MARKS for s in t.get("segments") or [])]
    return max(tracks, key=lambda t: len(t.get("segments") or []), default=None)


def _keyword_subtitles(draft, opts: dict, log: Callable) -> int:
    track = _subtitle_track(draft)
    if not track:
        log("   ! Không thấy track phụ đề để rút từ khoá")
        return 0
    # Lấy câu từ đúng track phụ đề (bản "auto" của AKP lấy cả badge TOP/giá).
    texts = {m.get("id"): m for m in draft.data["materials"].get("texts", [])}
    items = []
    for s in track["segments"]:
        try:
            text = json.loads(texts.get(s.get("material_id"), {}).get("content") or "{}").get("text", "")
        except ValueError:
            text = ""
        tr = s.get("target_timerange") or {}
        if text.strip():
            start = int(tr.get("start", 0))
            items.append({"start": start, "end": start + int(tr.get("duration", 0)), "text": " ".join(text.split())})
    items.sort(key=lambda it: it["start"])
    sub_ids = {s.get("id") for s in track["segments"]}
    n = akp_subtitle.build_keyword_subtitles(
        akp_engine, draft, items, str(_AKP_DIR),
        style_name=opts["sub_style"], max_words=opts["sub_max_words"],
        upper=opts["sub_upper"], log=log,
    )
    if n:
        for s in track["segments"]:
            s["visible"] = False
            s.setdefault("clip", {})["alpha"] = 0.0
        # ghi vào state để lần chạy sau AKP tự bật lại (unhide_original_subtitles)
        draft.state["hidden_subs"] = sorted(set(draft.state.get("hidden_subs") or []) | sub_ids)
        log(f"   + Đã ẩn {len(sub_ids)} câu phụ đề gốc (badge TOP/giá giữ nguyên)")
    return n


def _overlay_png(draft_dir: Path, name: str) -> str:
    """Chép PNG vào Resources của draft — đường dẫn ghi thẳng vào draft nên
    không được trỏ ra ngoài (dời thư mục là CapCut báo thiếu media)."""
    dst = draft_dir / "Resources" / "akp" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_AKP_DIR / "assets" / name, dst)
    return str(dst)


def enhance_draft(draft_dir: Path, options: Optional[dict] = None, log: Callable = print) -> None:
    """Áp Auto Keyframe Pro lên 1 draft CapCut. `options` ghi đè DEFAULT_OPTIONS."""
    draft_dir = Path(draft_dir)
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    pro_opts = {k: opts[k] for k in akp_engine.PRO_DEFAULTS}
    akp_engine.process_pro(str(draft_dir), pro_opts, log=log)

    if not (opts["keyword_subs"] or opts["letterbox"] or opts["vignette"]):
        return
    # process_pro đã lưu; mở lại để thêm phần process_pro không làm.
    draft = akp_engine.Draft(str(draft_dir))
    if opts["keyword_subs"]:
        _keyword_subtitles(draft, opts, log)
    if opts["vignette"]:
        akp_engine.add_overlay_track(draft, _overlay_png(draft_dir, "vignette_19_6.png"),
                                     alpha=opts["vignette_alpha"], name="AKP_VIGNETTE", log=log)
    if opts["letterbox"]:
        akp_engine.add_overlay_track(draft, _overlay_png(draft_dir, "letterbox_19_6.png"),
                                     alpha=1.0, name="AKP_LETTERBOX_19_6", log=log)
    akp_engine.renumber_tracks(draft)
    draft.save()
    akp_engine.verify_saved(str(draft_dir), log)
