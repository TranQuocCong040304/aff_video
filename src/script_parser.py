"""Tách kịch bản (04_script.txt) thành các đoạn theo Hook / từng sản phẩm / CTA,
và (khi có) tách tiếp mỗi đoạn sản phẩm thành từng KHỐI gắn với 1 ảnh cụ thể.

Dựa vào các mốc trong prompts/04b_product_segment.md:
- Mỗi đoạn nằm trong 1 cặp [Audio - Lời đọc] ... [/Audio - Lời đọc] riêng.
- Ngay trước đoạn sản phẩm có dòng "### SẢN PHẨM: <tên>".
- Bên trong đoạn sản phẩm (nếu Gemini có ảnh để mô tả), mỗi câu ứng với 1 ảnh
  được đánh dấu bằng dòng "[[IMG:n]]" — n là số thứ tự ảnh (1-based) khớp với
  thứ tự ảnh đã gửi cho Gemini (xem src/pipeline.py, src/product_links.py).
"""
import re
from dataclasses import dataclass
from typing import List, Optional

_TAG_RE = re.compile(r"(?<!\[)\[[^\[\]]*\](?!\])")  # thẻ cảm xúc: [cười nhẹ]... (không khớp [[IMG:n]])
_SEGMENT_RE = re.compile(
    r"(?:###\s*SẢN PHẨM:\s*(?P<product>.+?)\s*\r?\n)?"
    r"\[Audio - Lời đọc\]\s*(?P<body>.*?)\s*\[/Audio - Lời đọc\]",
    re.DOTALL,
)
_IMG_MARKER_RE = re.compile(r"\[\[IMG:(\d+)\]\]\s*")


def clean_narration(text: str) -> str:
    """Bỏ thẻ cảm xúc trong ngoặc vuông — dùng cho hiển thị/manifest, KHÔNG
    dùng để đưa vào TTS nữa (TTS giờ đọc trực tiếp text còn nguyên thẻ để lấy
    cảm xúc, xem src/tts.py: _strip_tags_track_pauses)."""
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def parse_image_blocks(raw_text: str) -> Optional[List[dict]]:
    """Tách 1 đoạn sản phẩm thành các khối theo marker [[IMG:n]]. Trả về None
    nếu không có marker nào (sản phẩm không có ảnh, hoặc LLM không tuân định
    dạng — pipeline.py đã lọc bỏ marker rác trong trường hợp đó)."""
    matches = list(_IMG_MARKER_RE.finditer(raw_text))
    if not matches:
        return None
    blocks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        blocks.append({"index": int(m.group(1)), "raw_text": raw_text[start:end].strip()})
    return blocks


@dataclass
class Segment:
    index: int
    kind: str  # "hook" | "product" | "cta"
    product_name: Optional[str]
    raw_text: str
    clean_text: str
    image_blocks: Optional[List[dict]] = None  # [{"index", "raw_text"}] khi có đánh dấu ảnh


def parse_segments(script: str) -> List[Segment]:
    matches = list(_SEGMENT_RE.finditer(script))
    if not matches:
        raise ValueError(
            "Không tìm thấy đoạn nào khớp [Audio - Lời đọc]...[/Audio - Lời đọc] trong script. "
            "Kiểm tra lại output của prompt 04b_product_segment.md."
        )

    product_positions = [i for i, m in enumerate(matches) if m.group("product")]
    first_product_idx = product_positions[0] if product_positions else len(matches)

    segments = []
    for i, m in enumerate(matches):
        product = m.group("product")
        if product:
            kind = "product"
        elif i < first_product_idx:
            kind = "hook"
        else:
            kind = "cta"
        raw = m.group("body").strip()

        image_blocks = parse_image_blocks(raw) if kind == "product" else None
        if image_blocks:
            clean_text = clean_narration(" ".join(b["raw_text"] for b in image_blocks))
        else:
            clean_text = clean_narration(raw)

        segments.append(
            Segment(
                index=i,
                kind=kind,
                product_name=product.strip() if product else None,
                raw_text=raw,
                clean_text=clean_text,
                image_blocks=image_blocks,
            )
        )
    return segments
