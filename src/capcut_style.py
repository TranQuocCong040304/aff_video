"""Bộ phong cách CapCut: học từ 1 project đã chỉnh tay trong CapCut, lưu thành
styles/<tên>.json, rồi áp cho mọi lần xuất draft sau (src/capcut_export.py).

Cách dùng cho mỗi kênh: xuất 1 video sang CapCut -> chỉnh tay đoạn đầu (chữ,
badge, nền, chuyển cảnh...) -> "Lưu phong cách từ project" -> các video sau
của kênh đó chọn đúng bộ phong cách này.

Nhận diện vai trò từng track trong project mẫu (không dựa vào id, vì project
mẫu có thể do người dùng tự dựng):
- video: >= 2 track thì track dưới cùng phóng to (scale > 1.2) là lớp nền;
  track video trên cùng là lớp ảnh chính
- text: chữ dạng "TOP n" -> badge TOP; dạng giá "149.000 đ" -> badge giá;
  mẫu caption (text_templates) hoặc track câu dài nhất -> phụ đề; track chữ
  ngắn nhiều đoạn -> chữ nhấn mạnh; track ít đoạn còn lại -> card lý do chọn
Mẫu lấy từ đoạn ĐẦU TIÊN của mỗi track — người dùng thường chỉnh phần đầu.
"""
import copy
import json
import re
from pathlib import Path
from typing import Optional

STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"

_TOP_RE = re.compile(r"^\s*TOP\s*\d+\s*$", re.IGNORECASE)
_PRICE_RE = re.compile(r"^\s*[\d.,]+\s*(đ|₫|k|vnđ)?\s*$", re.IGNORECASE)

DEFAULT_KEN_BURNS = {
    "enabled": True,
    "zoom_per_sec": 0.012, "zoom_range": [0.06, 0.20],
    "pan_per_sec": 0.015, "pan_range": [0.08, 0.20],
    "video_factor": 0.5,
}


def list_styles() -> list:
    return sorted(p.stem for p in STYLES_DIR.glob("*.json")) if STYLES_DIR.exists() else []


def load_style(name: str) -> dict:
    return json.loads((STYLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def save_style(style: dict, name: str) -> Path:
    STYLES_DIR.mkdir(parents=True, exist_ok=True)
    path = STYLES_DIR / f"{name}.json"
    path.write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _index(content: dict) -> dict:
    return {x["id"]: (k, x) for k, v in content["materials"].items() if isinstance(v, list)
            for x in v if isinstance(x, dict) and "id" in x}


def _strip_ids(material: dict) -> dict:
    m = copy.deepcopy(material)
    m.pop("id", None)
    return m


def _text_sample(seg: dict, material: dict, index: dict) -> dict:
    """Mẫu chữ = material đầy đủ (font, viền, nền...) + style đầu tiên trong
    content + vị trí/cỡ trên khung hình + hiệu ứng chữ đi kèm (vd DeepGlow)."""
    styles = json.loads(material["content"]).get("styles") or [{}]
    effects = [_strip_ids(index[r][1]) for r in seg.get("extra_material_refs", [])
               if r in index and index[r][0] == "effects"]
    return {
        "material": _strip_ids(material),
        "style": {k: v for k, v in styles[0].items() if k != "range"},
        "clip": copy.deepcopy(seg["clip"]),
        "effects": effects,
    }


def _caption_sample(seg: dict, template: dict, index: dict) -> Optional[dict]:
    """Mẫu caption của CapCut (text_templates) chỉ là 1 material chữ bên trong
    + animation — lấy material chữ đó làm mẫu phụ đề tĩnh trông giống hệt."""
    for res in template.get("text_info_resources", []):
        if res.get("clip_type") == "can_not_render":
            continue
        found = index.get(res.get("text_material_id"))
        if found and found[0] == "texts":
            sample = _text_sample(seg, found[1], index)
            sample["source_caption_template"] = template.get("name", "")
            return sample
    return None


def _first_text(track: dict, index: dict) -> str:
    seg = track["segments"][0]
    kind, mat = index[seg["material_id"]]
    if kind == "texts":
        return json.loads(mat["content"]).get("text", "")
    return ""


def _avg_words(track: dict, index: dict) -> float:
    counts = []
    for seg in track["segments"]:
        kind, mat = index[seg["material_id"]]
        if kind == "texts":
            counts.append(len(json.loads(mat["content"]).get("text", "").split()))
        elif kind == "text_templates":
            # Chữ caption nằm ở material chữ bên trong mẫu (current_word_info thường rỗng).
            for res in mat.get("text_info_resources", []):
                found = index.get(res.get("text_material_id"))
                if res.get("clip_type") != "can_not_render" and found and found[0] == "texts":
                    counts.append(len(json.loads(found[1]["content"]).get("text", "").split()))
                    break
    return sum(counts) / len(counts) if counts else 0.0


def extract_style(content: dict, name: str, description: str = "", source: str = "") -> dict:
    """Học bộ phong cách từ draft_content.json của 1 project đã chỉnh tay."""
    index = _index(content)
    style = {"name": name, "description": description, "source_draft": source,
             "background_layer": {"enabled": False}, "ken_burns": dict(DEFAULT_KEN_BURNS),
             "transitions": {"enabled": False, "pool": []}, "texts": {}}

    video_tracks = [t for t in content["tracks"] if t["type"] == "video" and t["segments"]]
    if len(video_tracks) >= 2:
        bg = video_tracks[0]["segments"][0]["clip"]
        if bg and bg["scale"]["x"] > 1.2:
            style["background_layer"] = {"enabled": True, "scale": bg["scale"]["x"], "alpha": bg["alpha"]}
    main = video_tracks[-1] if video_tracks else None
    if main:
        style["ken_burns"]["enabled"] = any(s.get("common_keyframes") for s in main["segments"])
        pool, seen = [], set()
        for seg in main["segments"]:
            for r in seg.get("extra_material_refs", []):
                kind, mat = index.get(r, (None, None))
                if kind == "transitions" and mat["name"] not in seen:
                    seen.add(mat["name"])
                    pool.append(_strip_ids(mat))
        style["transitions"] = {"enabled": bool(pool), "pool": pool}

    remaining = []
    for track in content["tracks"]:
        if track["type"] != "text" or not track["segments"]:
            continue
        seg = track["segments"][0]
        kind, mat = index[seg["material_id"]]
        if kind == "text_templates":
            sample = _caption_sample(seg, mat, index)
            if sample:
                sample["max_words"] = max(4, round(_avg_words(track, index)))
                style["texts"]["subtitle"] = sample
            continue
        if kind != "texts":
            continue
        first = _first_text(track, index)
        if _TOP_RE.match(first):
            style["texts"]["top_badge"] = _text_sample(seg, mat, index)
        elif _PRICE_RE.match(first):
            style["texts"]["price_badge"] = _text_sample(seg, mat, index)
        else:
            remaining.append(track)

    # Track câu dài nhất là phụ đề (nếu chưa có caption), track chữ ngắn
    # nhiều đoạn là chữ nhấn mạnh, track ít đoạn là card lý do chọn.
    remaining.sort(key=lambda t: _avg_words(t, index), reverse=True)
    for track in remaining:
        seg = track["segments"][0]
        sample = _text_sample(seg, index[seg["material_id"]][1], index)
        if "subtitle" not in style["texts"] and _avg_words(track, index) >= 8:
            sample["max_words"] = 0  # 0 = giữ nguyên cả câu như bản gốc
            style["texts"]["subtitle"] = sample
        elif len(track["segments"]) <= 10 and "intro" not in style["texts"]:
            style["texts"]["intro"] = sample
        elif "highlight" not in style["texts"]:
            style["texts"]["highlight"] = sample
    # Vai trò người dùng đã xoá khỏi project mẫu -> tắt luôn khi xuất.
    for role in ("subtitle", "highlight", "top_badge", "price_badge", "intro"):
        style["texts"].setdefault(role, {"enabled": False})
    return style


def describe_style(style: dict) -> list:
    """Tóm tắt dễ đọc để hiện trên UI."""
    lines = []
    bg = style.get("background_layer", {})
    lines.append(f"Lớp nền phóng to: {'có, x%.2f, độ mờ %d%%' % (bg['scale'], round(bg['alpha'] * 100)) if bg.get('enabled') else 'không'}")
    lines.append(f"Zoom + trượt ảnh: {'có' if style.get('ken_burns', {}).get('enabled') else 'không'}")
    pool = style.get("transitions", {}).get("pool", [])
    lines.append(f"Chuyển cảnh: {', '.join(t['name'] for t in pool) if pool else 'không'}")
    names = {"subtitle": "Phụ đề", "highlight": "Chữ nhấn mạnh", "top_badge": "Badge TOP",
             "price_badge": "Badge giá", "intro": "Card lý do chọn"}
    for role, label in names.items():
        sample = style.get("texts", {}).get(role, {})
        if sample.get("enabled") is False:
            lines.append(f"{label}: tắt")
            continue
        st = sample.get("style", {})
        fx = ", ".join(e.get("name", "") for e in sample.get("effects", [])) or "không"
        pos = sample.get("clip", {}).get("transform", {})
        lines.append(f"{label}: cỡ {st.get('size')}, vị trí ({pos.get('x', 0):.2f}, {pos.get('y', 0):.2f}), hiệu ứng {fx}")
    return lines
