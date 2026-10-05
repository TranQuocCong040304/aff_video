"""Ghép 08_youtube_description.txt — CHỈ chạy được sau khi có audio (Bước 2,
xem src/media_step.py) vì cần thời lượng THẬT từng đoạn để tính mốc thời gian
(timestamp), thứ mà lúc viết script (05_metadata.json, chỉ có phần sáng tạo:
tiêu đề/hook/tags) chưa thể biết được.

Tên sản phẩm trong link/timestamp LUÔN lấy nguyên văn từ 03_products_selected.json
(dữ liệu Shopee thật) — KHÔNG để LLM tự đặt biệt danh/tagline riêng cho từng
sản phẩm, vì đã gặp thật: LLM từng gán nhầm tính năng của sản phẩm A cho
timestamp của sản phẩm B khi viết tự do. Tagline hiển thị trong timestamp
trích thẳng từ `reason_selected` (câu lý do chọn ĐÚNG sản phẩm đó, đã dùng ở
Bước 1) thay vì để LLM viết lại.

Định dạng phỏng theo các video "Top 5 review" phổ biến trên YouTube: đoạn mở
đầu -> danh sách link mua hàng -> mốc thời gian -> nhắc like/subscribe -> hashtag.
"""
import json
import re
from pathlib import Path
from typing import Optional

_HASHTAG_STRIP_RE = re.compile(r"[^\w]+", re.UNICODE)


def _hashtag(tag: str) -> str:
    return "#" + _HASHTAG_STRIP_RE.sub("", tag)


def _fmt_time(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _short_reason(reason: str, max_len: int = 55) -> str:
    """Cắt reason_selected (đã dùng đúng cho sản phẩm này ở Bước 1) thành 1
    cụm ngắn cho dòng timestamp — cắt tại dấu chấm/phẩy đầu tiên trong giới
    hạn để không cụt giữa câu, thay vì để LLM viết lại (dễ lẫn sản phẩm)."""
    reason = reason.strip()
    if len(reason) <= max_len:
        return reason
    cut = reason[:max_len]
    for sep in (". ", ", "):
        idx = cut.rfind(sep)
        if idx > 15:
            return cut[:idx]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 15 else cut) + "..."


def _segment_duration(seg: dict) -> float:
    """Thời lượng thật của 1 đoạn = mốc kết thúc của beat cuối cùng (các beat
    đã được chuẩn hoá liền mạch từ 0 -> hết trong src/tts.py)."""
    beats = seg.get("beats") or []
    return max((b["end"] for b in beats), default=0.0)


def build_youtube_description(run_dir: Path) -> Optional[str]:
    """Trả về None nếu chưa đủ dữ liệu (chưa chạy Bước 2, hoặc thiếu
    03_products_selected.json) — gọi an toàn ngay sau run_media_step()."""
    manifest_path = run_dir / "06_media_manifest.json"
    metadata_path = run_dir / "05_metadata.json"
    products_path = run_dir / "03_products_selected.json"
    if not manifest_path.exists() or not products_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    products = json.loads(products_path.read_text(encoding="utf-8")).get("selected_products", [])
    products_by_name = {p["name"]: p for p in products}

    # 🛒 Danh sách link — ĐÚNG thứ tự xuất hiện trong video (theo manifest,
    # không phải theo rank) để khớp với timestamp bên dưới. Luôn dùng tên
    # sản phẩm THẬT (không để LLM tự đặt biệt danh) — tránh sai lệch tên khi
    # người xem đối chiếu với link Shopee thật.
    link_lines = []
    for seg in manifest:
        if seg.get("kind") != "product":
            continue
        product = products_by_name.get(seg.get("product_name") or "")
        if not product or not product.get("affiliate_link"):
            continue
        link_lines.append(f"{product['name']}:\n{product['affiliate_link']}")

    # ⏱️ Timestamps — cộng dồn thời lượng thật từng đoạn, chỉ gắn nhãn cho
    # đoạn hook/cta ĐẦU TIÊN (tránh lặp "Mở đầu"/"Tổng kết" nếu script có
    # nhiều khối hook/cta).
    ts_lines = []
    t = 0.0
    hook_labeled = cta_labeled = False
    for seg in manifest:
        dur = _segment_duration(seg)
        label = None
        if seg.get("kind") == "hook" and not hook_labeled:
            label = metadata.get("hook_label") or "Mở đầu"
            hook_labeled = True
        elif seg.get("kind") == "cta" and not cta_labeled:
            label = metadata.get("outro_label") or "Tổng kết"
            cta_labeled = True
        elif seg.get("kind") == "product":
            product = products_by_name.get(seg.get("product_name") or "")
            if product:
                rank = product.get("rank")
                name = product["name"]
                short_name = name if len(name) <= 45 else name[:42] + "..."
                reason = product.get("reason_selected")
                label = f"Top {rank}: {short_name}" + (f" - {_short_reason(reason)}" if reason else "")
        if label:
            ts_lines.append(f"{_fmt_time(t)} - {label}")
        t += dur

    hashtags = " ".join(_hashtag(tag) for tag in metadata.get("tags", []) if tag.strip())

    parts = []
    if metadata.get("hook_description"):
        parts.append(metadata["hook_description"])
    if link_lines:
        parts.append("🛒 Link mua hàng (nhớ đổi sang link đã gắn tag affiliate trước khi đăng):\n\n" + "\n\n".join(link_lines))
    if ts_lines:
        parts.append("⏱️ Các cột mốc thời gian (Timestamps):\n" + "\n".join(ts_lines))
    parts.append(
        "🔔 Đừng quên bấm LIKE và SUBSCRIBE để không bỏ lỡ các video review sản phẩm hay ho tiếp theo trên kênh của mình nhé!"
    )
    if hashtags:
        parts.append(hashtags)

    return "\n\n".join(parts)
