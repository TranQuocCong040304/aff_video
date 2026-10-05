"""Bước 3 (Video Assembly): ghép 06_media_manifest.json thành 1 file MP4 hoàn
chỉnh bằng MoviePy.

Mỗi đoạn (hook/sản phẩm/CTA) được chia theo "beats" — các mảnh nhỏ do
src/tts.py sinh ra (mỗi thẻ cảm xúc = 1 beat, có timing chính xác). Mỗi beat:
- Hiện ĐÚNG ảnh mà nó mô tả nếu beat có gắn ảnh cụ thể (từ [[IMG:n]] — xem
  src/pipeline.py, src/script_parser.py); nếu không, luân chuyển qua ảnh thật
  còn lại (sản phẩm) hoặc ảnh montage từ cả 5 sản phẩm (hook/CTA).
- Phụ đề chỉ hiện đúng câu/cụm của beat đó đang đọc.

Mỗi khung hình (nền + ảnh sản phẩm + badge + phụ đề) được ghép PHẲNG thành 1
ảnh RGB duy nhất bằng Pillow (1 lần, không animate) rồi mới đưa vào MoviePy.

Đã đo thực tế: MoviePy/PIL blend alpha theo từng frame ở độ phân giải HD RẤT
chậm — Ken Burns animate resize chậm ~9 lần (32.6s vs 3.5s/clip), tách lớp
CompositeVideoClip chậm ~9.4 lần (185.9s vs 19.7s/8 clip), crossfade (method=
compose) chậm ~4.7 lần (92.7s vs 19.7s/8 clip). Với hàng chục beat/video, bất
kỳ phép nào trong 3 phép này đều đẩy thời gian ghép lên hàng giờ (đã gặp thật:
2+ tiếng chưa xong). Nên bỏ hết vfx/composite/crossfade của MoviePy; zoom
mượt theo từng frame được tự dựng bằng Pillow theo cách rẻ (phóng sẵn ảnh 1
lần + affine sub-pixel chỉ trên vùng ảnh sản phẩm) — xem _ZOOM_MAX.

Hiệu ứng "punch" định kỳ (mỗi EFFECT_INTERVAL giây) CŨNG tránh animate resize
theo frame vì lý do trên — thử vfx.Resize trên 1 đoạn ngắn vẫn làm khung hình
LỚN hơn canvas, và concatenate_videoclips (method mặc định "chain") không tự
sửa kích thước khác nhau giữa các clip -> khung hình bị lệch/chồng (đã gặp
thật, xem ảnh preview lỗi). Đổi sang hiệu ứng "flash sáng" bằng phép nhân màu
thuần numpy ngay trong frame — không đổi kích thước khung hình nên an toàn khi
nối clip, và rẻ hơn resize rất nhiều.
"""
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
import proglog
from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:  # tuỳ chọn — chỉ để zoom mượt nhanh hơn, thiếu thì dùng PIL (xem _warp_region)
    import cv2
except ImportError:
    cv2 = None

from .product_links import find_product_media, list_all_product_images
from .utils import format_price, match_by_name

BG_COLOR = (17, 24, 21, 255)  # nền tối trung tính, đồng bộ màu với bản kế hoạch
CAPTION_BG = (0, 0, 0, 165)
TOP_BADGE_BG = (230, 163, 95, 235)
PRICE_BADGE_BG = (47, 111, 94, 235)

# Chữ nhấn mạnh đè lên phụ đề (kiểu CapCut các video Top 5 hay dùng) — không có
# nền, chỉ viền đen dày để nổi trên mọi ảnh nền, xem src/media_step.py:
# _extract_highlights (1 lệnh gọi Groq/video để sinh cụm từ này).
HIGHLIGHT_COLOR = (255, 205, 40, 255)
HIGHLIGHT_STROKE = (10, 10, 10, 255)

# Card "vì sao chọn" hiện ở câu đầu mỗi đoạn sản phẩm, dùng lại reason_selected
# đã có sẵn (không tốn thêm request LLM nào).
INTRO_CARD_BG = (47, 111, 94, 220)

# Zoom-in MƯỢT theo TỪNG FRAME (trước đây xấp xỉ bằng tối đa 40 ảnh tĩnh/câu
# -> chỉ ~10 bước/giây + kích thước làm tròn pixel nguyên nên thấy giật bậc).
# Không dùng vfx.Resize của MoviePy (chậm ~9 lần, xem đầu file) mà tự dựng
# frame rẻ hơn nhiều: ảnh sản phẩm được phóng sẵn 1 lần (LANCZOS) ở mức zoom
# lớn nhất, mỗi frame chỉ thu nhỏ nhẹ bằng phép affine BILINEAR với toạ độ
# SUB-PIXEL (không nhảy từng pixel), overlay cắt theo vùng có nội dung rồi
# mới ghép. Zoom chạy theo đường cong ease-in-out xuyên suốt cả "shot" (nhiều
# câu liên tiếp dùng chung 1 ảnh), tốc độ đều giữa shot dài/ngắn.
_ZOOM_MAX = 0.12            # biên độ zoom tối đa trong 1 shot (+12%)
_ZOOM_SPEED = 0.035         # mức zoom/giây — shot ngắn zoom ít lại cho khỏi "giật"
_ZOOM_FOCUS_Y = 0.35        # tâm zoom ở 35% chiều cao ảnh (phần thân sản phẩm), không phải mép trên

BACKDROP_BLUR_RADIUS = 40   # nền mờ phóng to từ chính ảnh đang hiện, lấp khoảng trống 2 bên
BACKDROP_DARKEN = 0.35      # tối bớt nền mờ để chữ/badge nổi rõ hơn

# Chèn video sản phẩm THẬT (nếu có) vào ĐÚNG 1 beat/sản phẩm — câu dài nhất
# trong đoạn đó, đủ thời gian để thấy chuyển động. Đã đo thực tế: video giải
# mã + resize + blend overlay chậm hơn ảnh tĩnh ~17 lần/giây (33s vs 2s cho
# 3s clip) — nên CHỈ dùng có chọn lọc (1 beat/sản phẩm), không phải mọi beat,
# để không kéo thời gian ghép lên quá nhiều. Video được cắt vừa khít thời
# lượng beat (không kéo dài video tổng), tắt tiếng (audio vẫn là giọng đọc),
# phủ kín khung hình kiểu B-roll thật thay vì ảnh nhỏ trên nền mờ.
_VIDEO_BEAT_MIN_DURATION = 1.5

# Hiệu ứng "punch" (flash sáng nhanh) cứ mỗi EFFECT_INTERVAL giây, dài
# EFFECT_PUNCH_DURATION giây — xem lý do dùng flash thay vì zoom ở đầu file.
EFFECT_INTERVAL = 12.0
EFFECT_PUNCH_DURATION = 0.18
EFFECT_PUNCH_PEAK_BRIGHTNESS = 1.5  # độ sáng tại đúng thời điểm punch, giảm dần về 1.0

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font(explicit: Optional[str] = None) -> str:
    if explicit and Path(explicit).exists():
        return explicit
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "Không tìm thấy font hỗ trợ tiếng Việt có dấu. Đặt biến môi trường VIDEO_FONT_PATH "
        "trỏ tới 1 file .ttf (vd C:\\Windows\\Fonts\\segoeui.ttf)."
    )


@lru_cache(maxsize=16)
def _get_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    """Cache font đã load theo (đường dẫn, cỡ chữ) — _draw_caption_layer gọi
    lại tới _MOTION_STEPS lần/câu (hiện chữ dần), load lại font từ đĩa mỗi lần
    là phí (mở file + parse font metrics lặp lại không cần thiết)."""
    return ImageFont.truetype(font_path, size)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_badge(overlay: Image.Image, draw: ImageDraw.ImageDraw, text: str, font_path: str,
                anchor: str, font_size: int = 40, pad: int = 18, margin: int = 40,
                bg=TOP_BADGE_BG) -> None:
    font = _get_font(font_path, font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = tw + pad * 2, th + pad * 2
    x0 = margin if anchor == "topleft" else overlay.width - box_w - margin
    y0 = margin
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=14, fill=bg)
    draw.text((x0 + pad - bbox[0], y0 + pad - bbox[1]), text, font=font, fill=(255, 255, 255, 255))


def _caption_layout(text: str, canvas: tuple, font_path: str, font_size: int = 44) -> dict:
    """Tính trước wrap/box 1 LẦN cho CẢ CÂU — cố định suốt beat dù chữ hiện
    dần từng từ (xem _draw_caption_layer), để box/vị trí highlight phía trên
    không bị nhảy khi câu ngắn dần lúc mới hiện. Vị trí X mỗi dòng neo theo
    ĐỘ RỘNG DÒNG ĐẦY ĐỦ nên các từ mới xuất hiện nối tiếp bên phải, không làm
    dịch chuyển từ đã hiện trước đó."""
    W, H = canvas
    font = _get_font(font_path, font_size)
    max_width = int(W * 0.82)
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap_text(text, font, max_width, scratch)
    line_height = int(font_size * 1.35)
    box_h = line_height * len(lines) + 50
    box_top = H - box_h - 50
    line_x = []
    for line in lines:
        bbox = scratch.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        line_x.append((W - tw) // 2 - bbox[0])
    return {
        "lines": lines, "box_top": box_top, "box_h": box_h,
        "line_height": line_height, "line_x": line_x, "font_size": font_size,
    }


def _draw_caption_layer(layout: dict, canvas: tuple, font_path: str,
                         reveal_word_count: Optional[int] = None) -> Image.Image:
    """Lớp RIÊNG chỉ có nền phụ đề + chữ — tách khỏi badge/highlight/card
    (xem _draw_static_layer) để vẽ lại rẻ mỗi bước hiện từ dần trong 1 beat.
    `reveal_word_count=None` hiện trọn câu ngay; nếu có, chỉ vẽ đủ số từ đó
    theo đúng thứ tự dòng đã wrap sẵn trong `layout`."""
    W, H = canvas
    font = _get_font(font_path, layout["font_size"])
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle([0, layout["box_top"], W, layout["box_top"] + layout["box_h"]], fill=CAPTION_BG)

    remaining = reveal_word_count
    y = layout["box_top"] + 25
    for line, x in zip(layout["lines"], layout["line_x"]):
        draw_line = line
        if remaining is not None:
            words = line.split()
            if remaining <= 0:
                draw_line = ""
            elif remaining < len(words):
                draw_line = " ".join(words[:remaining])
            remaining -= len(words)
        if draw_line:
            bbox = draw.textbbox((0, 0), draw_line, font=font)
            draw.text((x, y - bbox[1]), draw_line, font=font, fill=(255, 255, 255, 255))
        y += layout["line_height"]
    return layer


def _draw_highlight(draw: ImageDraw.ImageDraw, text: str, canvas: tuple, font_path: str,
                     bottom_y: int, font_size: int = 56) -> None:
    """Cụm từ nhấn mạnh to, màu vàng, viền đen — không nền, neo đáy tại
    `bottom_y` (= box_top của phụ đề bên dưới) để 2 lớp chữ không chồng lên
    nhau, giống style CapCut trong video mẫu."""
    W, _ = canvas
    font = _get_font(font_path, font_size)
    lines = _wrap_text(text, font, int(W * 0.85), draw)
    line_height = int(font_size * 1.25)
    y = bottom_y - 20 - line_height * len(lines)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        draw.text((x - bbox[0], y - bbox[1]), line, font=font, fill=HIGHLIGHT_COLOR,
                   stroke_width=4, stroke_fill=HIGHLIGHT_STROKE)
        y += line_height


def _draw_intro_card(overlay: Image.Image, draw: ImageDraw.ImageDraw, text: str, font_path: str,
                      canvas: tuple, font_size: int = 30, top_y: int = 140) -> None:
    """Card ngắn tóm tắt lý do chọn sản phẩm (reason_selected có sẵn, không
    tốn thêm request LLM) — chỉ hiện ở câu đầu mỗi đoạn sản phẩm."""
    W, _ = canvas
    font = _get_font(font_path, font_size)
    max_width = int(W * 0.7)
    lines = _wrap_text(text, font, max_width - 60, draw)[:2]
    line_height = int(font_size * 1.3)
    box_w = max_width
    box_h = line_height * len(lines) + 36
    x0 = (W - box_w) // 2
    y0 = top_y
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=16, fill=INTRO_CARD_BG)
    y = y0 + 18
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        draw.text((x - bbox[0], y - bbox[1]), line, font=font, fill=(255, 255, 255, 255))
        y += line_height


@lru_cache(maxsize=32)
def _make_backdrop(image_path: Optional[Path], canvas: tuple) -> Image.Image:
    """Nền = chính ảnh đang hiện, phóng to phủ kín khung hình + làm mờ + tối
    bớt — thay cho nền trơn, lấp khoảng trống 2 bên khi ảnh sản phẩm không
    cùng tỉ lệ khung hình. Cache theo (image_path, canvas) vì render_frame giờ
    gọi lại nhiều lần/câu (mỗi bước zoom-in) với CÙNG 1 ảnh — Gaussian blur là
    phần tốn nhất, không cần làm lại. An toàn cache vì render_frame luôn
    .convert("RGBA") ra bản sao mới, không sửa trực tiếp ảnh trả về ở đây."""
    W, H = canvas
    if not image_path:
        return Image.new("RGB", (W, H), BG_COLOR[:3])
    img = Image.open(image_path).convert("RGB")
    scale = max(W / img.width, H / img.height)
    new_size = (max(1, int(img.width * scale) + 1), max(1, int(img.height * scale) + 1))
    img = img.resize(new_size, Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img = img.filter(ImageFilter.GaussianBlur(radius=BACKDROP_BLUR_RADIUS))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.blend(img, dark, BACKDROP_DARKEN)


def _draw_static_layer(canvas: tuple, font_path: str, product_info: Optional[dict], has_image: bool,
                        highlight_text: Optional[str], intro_text: Optional[str],
                        caption_box_top: int) -> Image.Image:
    """Badge + chữ nhấn mạnh + card — KHÔNG gồm phụ đề (tách riêng vì phụ đề
    đổi theo từng từ hiện dần trong 1 beat, xem _draw_caption_layer). Phần
    này GIỐNG HỆT nhau giữa các bước zoom trong cùng 1 beat nên _beat_clip chỉ
    dựng 1 lần rồi copy+ghép caption riêng mỗi bước, rẻ hơn dựng lại cả overlay."""
    W, H = canvas
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    if has_image and product_info:
        if product_info.get("rank"):
            _draw_badge(layer, draw, f"TOP {product_info['rank']}", font_path, anchor="topleft", bg=TOP_BADGE_BG)
        if product_info.get("price") is not None:
            _draw_badge(layer, draw, format_price(product_info["price"]), font_path,
                        anchor="topright", bg=PRICE_BADGE_BG)

    if intro_text:
        _draw_intro_card(layer, draw, intro_text, font_path, (W, H))

    if highlight_text:
        _draw_highlight(draw, highlight_text, (W, H), font_path, bottom_y=caption_box_top)

    return layer


def _render_overlay(caption_text: str, canvas: tuple, font_path: str, product_info: Optional[dict],
                     has_image: bool, highlight_text: Optional[str] = None,
                     intro_text: Optional[str] = None, reveal_word_count: Optional[int] = None) -> Image.Image:
    """Ghép lớp tĩnh (badge/highlight/card) + lớp phụ đề thành 1 overlay đầy
    đủ — dùng cho render_frame() (API dựng 1 khung đơn lẻ). `_beat_clip` tự
    ghép 2 lớp này riêng mỗi bước thay vì gọi hàm này lặp lại, xem ở đó."""
    W, H = canvas
    caption_layout = _caption_layout(caption_text, canvas, font_path) if caption_text else None
    caption_box_top = caption_layout["box_top"] if caption_layout else H - 50

    overlay = _draw_static_layer(canvas, font_path, product_info, has_image, highlight_text, intro_text,
                                  caption_box_top)
    if caption_layout:
        overlay.alpha_composite(_draw_caption_layer(caption_layout, canvas, font_path, reveal_word_count))

    return overlay


def _compose_frame(image_path: Optional[Path], canvas: tuple, zoom: float, overlay: Image.Image) -> Image.Image:
    """Nền (cache theo ảnh) + ảnh sản phẩm ở đúng zoom + overlay đã dựng sẵn —
    phần RẺ, lặp lại mỗi bước zoom trong 1 câu (xem _render_overlay)."""
    W, H = canvas
    base = _make_backdrop(image_path, canvas).convert("RGBA")

    if image_path:
        product_img = Image.open(image_path).convert("RGBA")
        max_w, max_h = int(W * 0.97 * zoom), int(H * 0.78 * zoom)
        ratio = min(max_w / product_img.width, max_h / product_img.height, 1.20 * zoom)
        new_size = (max(1, int(product_img.width * ratio)), max(1, int(product_img.height * ratio)))
        product_img = product_img.resize(new_size, Image.LANCZOS)
        pos = ((W - new_size[0]) // 2, int(H * 0.03))
        base.alpha_composite(product_img, pos)

    base.alpha_composite(overlay)
    return base.convert("RGB")


def render_frame(image_path: Optional[Path], caption_text: str, canvas: tuple, font_path: str,
                  product_info: Optional[dict], zoom: float = 1.0,
                  highlight_text: Optional[str] = None, intro_text: Optional[str] = None) -> Image.Image:
    """Ghép PHẲNG 1 khung hình hoàn chỉnh (nền mờ + ảnh sản phẩm sắc nét + badge
    + phụ đề) bằng Pillow — dựng overlay + compose trong 1 lần gọi. `_beat_clip`
    KHÔNG gọi hàm này lặp lại mỗi bước zoom (xem _render_overlay/_compose_frame)
    mà tự dựng overlay 1 lần rồi compose nhiều lần cho rẻ hơn."""
    overlay = _render_overlay(caption_text, canvas, font_path, product_info, has_image=bool(image_path),
                               highlight_text=highlight_text, intro_text=intro_text)
    return _compose_frame(image_path, canvas, zoom, overlay)


def _build_beats(seg: dict, duration: float) -> List[dict]:
    """Ưu tiên "beats" (mới, do tts.py sinh — đã đúng theo cảm xúc + ảnh gắn
    sẵn). Manifest cũ chỉ có "sentence_timings" thì fallback về khối lớn theo
    câu, không có ảnh gắn theo beat (video_assembly tự luân chuyển ảnh)."""
    beats = seg.get("beats")
    if beats:
        out = [
            {"text": b["text"], "start": b["start"], "end": b["end"], "image": b.get("image"),
             "highlight": b.get("highlight")}
            for b in beats
        ]
    else:
        sentence_timings = seg.get("sentence_timings")
        if sentence_timings:
            out = [{"text": s["text"], "start": s["start"], "end": s["end"], "image": None} for s in sentence_timings]
        else:
            out = [{"text": seg.get("text", ""), "start": 0.0, "end": duration, "image": None}]

    out.sort(key=lambda c: c["start"])
    out[0]["start"] = 0.0
    for i in range(len(out) - 1):
        out[i]["end"] = out[i + 1]["start"]
    out[-1]["end"] = duration
    out = [c for c in out if c["end"] > c["start"]]
    return out or [{"text": seg.get("text", ""), "start": 0.0, "end": duration, "image": None}]


def _pick_fallback_image(images: List[Path], index: int, total: int) -> Optional[Path]:
    if not images:
        return None
    idx = min(int(index * len(images) / max(total, 1)), len(images) - 1)
    return images[idx]


def _collect_montage_images(products_data: dict, run_dir: Path, max_per_product: int = 2) -> List[Path]:
    """Ảnh nền cho hook/CTA khi không có ảnh minh họa riêng cho 2 đoạn này —
    lấy vài ảnh thật của TỪNG sản phẩm (theo thứ tự rank) thay vì nền trơn,
    kiểu montage giới thiệu tổng quan. Miễn phí, không cần Veo/Google Flow."""
    montage: List[Path] = []
    for p in products_data.get("selected_products", []):
        montage.extend(list_all_product_images(p, run_dir)[:max_per_product])
    return montage


def _ease_in_out(p: float) -> float:
    """Đường cong sin: khởi động và dừng êm, giữa shot chạy đều — mắt thấy
    "trôi" thay vì zoom tuyến tính cứng."""
    p = min(max(p, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * p)


def shot_zoom_range(shot_duration: float) -> float:
    """Biên độ zoom cho 1 shot: tỉ lệ thời lượng (tốc độ đều _ZOOM_SPEED/giây),
    chặn trên ở _ZOOM_MAX — shot 1s không bị zoom vọt 12% trong chớp mắt."""
    return min(_ZOOM_MAX, _ZOOM_SPEED * max(shot_duration, 0.0))


_PRODUCT_PAD = 2  # viền quanh ảnh phóng sẵn — để vùng lấy mẫu sub-pixel không vượt mép ảnh


@lru_cache(maxsize=8)
def _backdrop_np(image_path: Optional[Path], canvas: tuple) -> np.ndarray:
    arr = np.array(_make_backdrop(image_path, canvas))
    arr.flags.writeable = False  # bản cache dùng chung — mỗi frame phải .copy()
    return arr


@lru_cache(maxsize=8)
def _prescaled_product(image_path: Path, canvas: tuple):
    """Ảnh sản phẩm phóng sẵn 1 lần (LANCZOS, chất lượng cao) ở mức zoom lớn
    nhất 1+_ZOOM_MAX, mỗi frame chỉ còn thu nhỏ nhẹ (>= 1/(1+_ZOOM_MAX), không
    lo răng cưa) bằng nội suy tuyến tính. Ảnh không trong suốt (JPG — đa số
    ảnh Shopee) giữ RGB để gán thẳng, khỏi alpha blend. Trả về (mảng numpy có
    viền _PRODUCT_PAD, có_alpha, kích thước ảnh phóng sẵn, kích thước float
    của ảnh ở zoom 1.0 — cùng công thức kích thước với _compose_frame)."""
    W, H = canvas
    img = Image.open(image_path).convert("RGBA")
    ratio = min(W * 0.97 / img.width, H * 0.78 / img.height, 1.20)
    base_w, base_h = img.width * ratio, img.height * ratio
    pre_size = (max(1, math.ceil(base_w * (1 + _ZOOM_MAX))), max(1, math.ceil(base_h * (1 + _ZOOM_MAX))))
    has_alpha = img.getchannel("A").getextrema()[0] < 255
    pre = img.resize(pre_size, Image.LANCZOS)
    if has_alpha:
        pre = pre.convert("RGBa")  # premultiplied: nội suy ở mép trong suốt không bị viền tối
    else:
        pre = pre.convert("RGB")
    arr = np.array(pre)
    pad = [(_PRODUCT_PAD, _PRODUCT_PAD), (_PRODUCT_PAD, _PRODUCT_PAD), (0, 0)]
    arr = np.pad(arr, pad, mode="constant" if has_alpha else "edge")
    return arr, has_alpha, pre_size, (base_w, base_h)


def _warp_region(src: np.ndarray, kx: float, ky: float, tx: float, ty: float, size: tuple) -> np.ndarray:
    """dst(u, v) = src((u - tx) / kx, (v - ty) / ky), nội suy tuyến tính,
    toạ độ thực (sub-pixel). OpenCV nhanh hơn PIL ~10 lần cho phép này; máy
    nào chưa cài opencv thì dùng PIL resize(box=...) — chậm hơn nhưng kết quả
    tương đương."""
    w, h = size
    if cv2 is not None:
        m = np.array([[kx, 0.0, tx], [0.0, ky, ty]], dtype=np.float64)
        return cv2.warpAffine(src, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    box = (-tx / kx, -ty / ky, (w - tx) / kx, (h - ty) / ky)
    box = (max(0.0, box[0]), max(0.0, box[1]), min(src.shape[1], box[2]), min(src.shape[0], box[3]))
    mode = "RGBa" if src.shape[2] == 4 else "RGB"
    return np.asarray(Image.fromarray(src, mode).resize((w, h), Image.BILINEAR, box=box))


def _premultiply(rgb: np.ndarray, alpha: np.ndarray, already: bool = False):
    """(rgb*a/255, 255-a) dạng uint8 3 kênh — tính 1 lần để blend mỗi frame
    chỉ còn 1 phép nhân + 1 phép cộng (xem _blend_into)."""
    a = alpha.astype(np.uint16)[:, :, None]
    premult = rgb if already else ((rgb.astype(np.uint16) * a + 127) // 255).astype(np.uint8)
    inv = np.repeat((255 - a).astype(np.uint8), 3, axis=2)
    return np.ascontiguousarray(premult), inv


def _blend_into(frame: np.ndarray, premult: np.ndarray, inv: np.ndarray, x: int, y: int) -> None:
    """frame = frame*(255-a)/255 + rgb*a/255 trên đúng 1 vùng nhỏ, tại chỗ."""
    h, w = inv.shape[:2]
    region = frame[y:y + h, x:x + w]
    if cv2 is not None:
        frame[y:y + h, x:x + w] = cv2.add(cv2.multiply(region, inv, scale=1 / 255), premult)
    else:
        frame[y:y + h, x:x + w] = (
            (region.astype(np.uint16) * inv + 127) // 255 + premult
        ).clip(0, 255).astype(np.uint8)


def _smooth_zoom_frame(image_path: Optional[Path], canvas: tuple, zoom: float, overlay_bands) -> np.ndarray:
    """Dựng 1 frame: nền mờ (cache) + ảnh sản phẩm ở `zoom` với toạ độ SUB-
    PIXEL (nội dung trôi mượt dưới 1 pixel/frame, không nhảy bậc) + các dải
    overlay đã cắt sẵn. Chỉ xử lý đúng vùng có nội dung, không blend cả khung
    1920x1080 nên rẻ hơn _compose_frame nhiều lần."""
    W, H = canvas
    frame = _backdrop_np(image_path, canvas).copy()

    if image_path:
        pre, has_alpha, (pw, ph), (base_w, base_h) = _prescaled_product(image_path, canvas)
        w_f, h_f = base_w * zoom, base_h * zoom
        x_f = (W - w_f) / 2
        y_f = H * 0.03 + base_h * _ZOOM_FOCUS_Y * (1 - zoom)
        x0, y0 = max(0, math.floor(x_f)), max(0, math.floor(y_f))
        x1, y1 = min(W, math.ceil(x_f + w_f)), min(H, math.ceil(y_f + h_f))
        if x1 > x0 and y1 > y0:
            kx, ky = w_f / pw, h_f / ph  # pixel khung hình / pixel ảnh phóng sẵn
            tx, ty = x_f - x0 - _PRODUCT_PAD * kx, y_f - y0 - _PRODUCT_PAD * ky
            region = _warp_region(pre, kx, ky, tx, ty, (x1 - x0, y1 - y0))
            if has_alpha:
                _blend_into(frame, *_premultiply(region[:, :, :3], region[:, :, 3], already=True), x0, y0)
            else:
                frame[y0:y1, x0:x1] = region

    for premult, inv, (x, y) in overlay_bands:
        _blend_into(frame, premult, inv, x, y)
    return frame


def _overlay_bands(layer: Image.Image) -> list:
    """Cắt overlay RGBA thành các dải ngang có nội dung (badge trên, chữ nhấn
    mạnh/phụ đề dưới...), mỗi dải lấy bbox riêng. Overlay gần như trong suốt toàn bộ nên blend nguyên khung rất phí; bỏ qua
    khoảng trống giữa các dải thì rẻ hơn hẳn. Mỗi dải: (rgb đã nhân alpha,
    255-alpha, vị trí) — xem _premultiply."""
    arr = np.array(layer)
    alpha = arr[:, :, 3]
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    if rows.size == 0:
        return []
    bands, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > 8:
            bands.append((start, prev + 1))
            start = r
        prev = r
    bands.append((start, prev + 1))

    out = []
    for top, bottom in bands:
        cols = np.flatnonzero(alpha[top:bottom].max(axis=0) > 0)
        left, right = int(cols[0]), int(cols[-1]) + 1
        band = arr[top:bottom, left:right]
        out.append((*_premultiply(band[:, :, :3], band[:, :, 3]), (left, int(top))))
    return out


def _beat_clip(image_path: Optional[Path], caption_text: str, canvas: tuple, font_path: str,
               product_info: Optional[dict], duration: float, shot_progress: tuple = (0.0, 1.0),
               zoom_range: Optional[float] = None, punch_offset: Optional[float] = None,
               highlight_text: Optional[str] = None, intro_text: Optional[str] = None) -> List:
    """Trả về [1 VideoClip] cho 1 beat, zoom-in mượt theo TỪNG FRAME (xem
    _ZOOM_MAX ở đầu file). `shot_progress` = (đầu, cuối) của beat này tính
    theo tỉ lệ 0..1 trong cả "shot" (nhiều beat liên tiếp dùng CHUNG 1 ảnh) và
    `zoom_range` = biên độ zoom của cả shot (shot_zoom_range) — để zoom chạy
    liên tục, cùng 1 đường cong ease-in-out xuyên suốt shot thay vì giật lùi
    về 1.0 mỗi khi đổi câu dù ảnh không đổi. Phụ đề hiện DẦN từng từ theo tiến
    độ thời gian trong beat (không có timing chính xác từng từ từ edge-tts nên
    xấp xỉ tuyến tính theo số từ, về đủ câu hơi sớm trước khi hết beat) — các
    lớp overlay theo từng số từ được dựng 1 lần rồi cache. Nếu `punch_offset`
    (giây, tính từ đầu beat) được set, chèn 1 cú flash sáng nhanh
    (EFFECT_PUNCH_DURATION giây) ngay trong frame tương ứng."""
    if zoom_range is None:
        zoom_range = shot_zoom_range(duration)
    p_start, p_end = shot_progress

    caption_layout = _caption_layout(caption_text, canvas, font_path) if caption_text else None
    caption_box_top = caption_layout["box_top"] if caption_layout else canvas[1] - 50
    n_words = len(caption_text.split()) if caption_text else 0
    static_layer = _draw_static_layer(canvas, font_path, product_info, bool(image_path),
                                       highlight_text, intro_text, caption_box_top)
    overlay_cache = {}

    def overlay_for(reveal: int):
        if reveal not in overlay_cache:
            layer = static_layer
            if caption_layout:
                layer = static_layer.copy()
                layer.alpha_composite(_draw_caption_layer(caption_layout, canvas, font_path, reveal))
            overlay_cache[reveal] = _overlay_bands(layer)
        return overlay_cache[reveal]

    def make_frame(t):
        local = min(max(t / duration, 0.0), 1.0) if duration > 0 else 1.0
        zoom = 1.0 + zoom_range * _ease_in_out(p_start + (p_end - p_start) * local)
        reveal = min(n_words, max(1, round(local * n_words * 1.1))) if n_words else 0
        frame = _smooth_zoom_frame(image_path, canvas, zoom, overlay_for(reveal))
        if punch_offset is not None and punch_offset <= t < punch_offset + EFFECT_PUNCH_DURATION:
            progress = (t - punch_offset) / EFFECT_PUNCH_DURATION
            brightness = EFFECT_PUNCH_PEAK_BRIGHTNESS - (EFFECT_PUNCH_PEAK_BRIGHTNESS - 1.0) * progress
            frame = np.clip(frame.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
        return frame

    return [VideoClip(make_frame, duration=duration)]


def _video_beat_clip(video_path: Path, duration: float, canvas: tuple, caption_text: str, font_path: str,
                      product_info: Optional[dict], highlight_text: Optional[str] = None,
                      intro_text: Optional[str] = None):
    """1 beat = video sản phẩm THẬT (tắt tiếng), phủ kín khung hình kiểu cover
    -fit (giống _make_backdrop nhưng bằng MoviePy thay vì Pillow), cắt vừa
    khít `duration` — không kéo dài video tổng. Overlay (badge/chữ nhấn mạnh/
    card) dựng 1 lần; phụ đề hiện dần từng từ như _beat_clip — precompute sẵn
    1 layer/mốc từ (rẻ, chỉ copy+composite) rồi CHỌN theo `t` lúc blend, không
    vẽ lại PIL mỗi frame (đắt hơn ảnh tĩnh nhiều — xem _VIDEO_BEAT_MIN_DURATION
    — nên hàm này chỉ nên gọi có chọn lọc, không phải mọi beat)."""
    W, H = canvas
    src = VideoFileClip(str(video_path)).without_audio()
    start = max(0.0, (src.duration - duration) / 2)  # đoạn giữa video, tránh đầu/cuối hay rung tay
    clip = src.subclipped(start, start + duration)
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resized(scale).cropped(width=W, height=H, x_center=clip.w * scale / 2, y_center=clip.h * scale / 2)

    caption_layout = _caption_layout(caption_text, canvas, font_path) if caption_text else None
    caption_box_top = caption_layout["box_top"] if caption_layout else H - 50
    static_layer = _draw_static_layer(canvas, font_path, product_info, True, highlight_text, intro_text,
                                       caption_box_top)
    n_words = len(caption_text.split()) if caption_text else 0

    def _layer_to_rgba_np(layer: Image.Image):
        arr = np.array(layer)
        return arr[:, :, :3].astype(np.float32), arr[:, :, 3:4].astype(np.float32) / 255.0

    if caption_layout:
        reveal_frames = []
        for w in range(1, n_words + 1):
            layer = static_layer.copy()
            layer.alpha_composite(_draw_caption_layer(caption_layout, canvas, font_path, w))
            reveal_frames.append(_layer_to_rgba_np(layer))
    else:
        reveal_frames = [_layer_to_rgba_np(static_layer)]

    def _blend(get_frame, t):
        frame = get_frame(t).astype(np.float32)
        idx = 0
        if n_words > 0:
            idx = min(n_words - 1, max(0, round(min(t / duration, 1.0) * n_words) - 1))
        rgb, alpha = reveal_frames[idx]
        return (frame * (1 - alpha) + rgb * alpha).astype(np.uint8)

    return clip.transform(_blend, apply_to=["video"])


class _EncodeProgressLogger(proglog.ProgressBarLogger):
    """Chuyển tiến độ encode của MoviePy (bar 'frame_index') sang progress_cb."""

    def __init__(self, callback: Callable[[str, float], None]):
        super().__init__()
        self._callback = callback

    def bars_callback(self, bar, attr, value, old_value=None):
        if bar != "frame_index" or attr != "index":
            return
        total = self.bars[bar].get("total")
        if total:
            # Dựng clip ~ 0-10%, encode ~ 10-100% (encode chiếm gần hết thời gian).
            self._callback("Đang encode MP4", 0.10 + 0.90 * min(value / total, 1.0))


def assemble_video(
    run_dir: Path,
    canvas: tuple = (1920, 1080),
    font_path: Optional[str] = None,
    fps: int = 30,
    out_name: str = "final_video.mp4",
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> Path:
    """progress_cb(phase, fraction 0..1): tuỳ chọn, để UI hiện tiến độ (xem src/render_job.py)."""
    manifest_path = run_dir / "06_media_manifest.json"
    products_path = run_dir / "03_products_selected.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Không tìm thấy {manifest_path} — chạy Bước 2 (media) trước.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    products_data = json.loads(products_path.read_text(encoding="utf-8")) if products_path.exists() else {}
    products_by_name = {p["name"]: p for p in products_data.get("selected_products", [])}
    font = find_font(font_path)
    montage_images = _collect_montage_images(products_data, run_dir)

    all_clips = []  # mọi clip (mini + đã ghép) để đóng lại ở finally
    segment_clips = []
    global_time = 0.0        # mốc thời gian toàn video, để chèn punch mỗi EFFECT_INTERVAL giây
    next_punch_at = EFFECT_INTERVAL
    try:
        for seg_idx, seg in enumerate(manifest):
            if progress_cb:
                progress_cb("Đang dựng các đoạn clip", 0.10 * seg_idx / max(len(manifest), 1))
            if not seg.get("audio_file"):
                print(f"[BỎ QUA] Đoạn thứ {seg.get('order')} ({seg.get('kind')}) không có audio.")
                continue

            audio_clip = AudioFileClip(str(run_dir / seg["audio_file"]))
            all_clips.append(audio_clip)
            duration = audio_clip.duration

            product_info = None
            fallback_images: List[Path] = []
            if seg.get("kind") == "product":
                product_info = match_by_name(seg.get("product_name") or "", products_by_name)
                if product_info:
                    fallback_images = list_all_product_images(product_info, run_dir)
                if not fallback_images and seg.get("image_file"):
                    fallback_images = [run_dir / seg["image_file"]]
            elif montage_images:
                fallback_images = montage_images

            beats = _build_beats(seg, duration)

            # Resolve ảnh cho từng beat trước, rồi gộp các beat LIÊN TIẾP dùng
            # CHUNG 1 ảnh thành 1 "shot" — zoom-in sẽ chạy liên tục xuyên suốt
            # cả shot (xem _beat_clip) thay vì giật lùi về 1.0 mỗi câu, vì
            # Gemini có thể tả 1 ảnh trong 2-3 câu liên tiếp.
            resolved = []
            for i, beat in enumerate(beats):
                beat_dur = beat["end"] - beat["start"]
                if beat_dur <= 0.01:
                    continue
                img = (run_dir / beat["image"]) if beat.get("image") else _pick_fallback_image(fallback_images, i, len(beats))
                resolved.append({"beat": beat, "index": i, "duration": beat_dur, "image": img})

            # Nếu sản phẩm có video thật, thay ĐÚNG 1 beat (câu dài nhất, đủ
            # thời gian thấy chuyển động) bằng video đó — xem _VIDEO_BEAT_MIN_DURATION.
            if seg.get("kind") == "product" and product_info and resolved:
                _, video_path = find_product_media(product_info, run_dir)
                if video_path:
                    try:
                        video_duration = VideoFileClip(str(video_path)).without_audio().duration
                    except Exception as e:
                        print(f"[CẢNH BÁO] Không đọc được video '{video_path.name}': {e} — bỏ qua, dùng ảnh tĩnh.")
                        video_duration = 0.0
                    longest = max(resolved, key=lambda r: r["duration"])
                    if longest["duration"] >= _VIDEO_BEAT_MIN_DURATION and video_duration >= longest["duration"] + 0.2:
                        longest["image"] = object()  # khoá riêng biệt -> tự thành 1 shot riêng
                        longest["video"] = video_path

            shots = []
            for item in resolved:
                if shots and shots[-1]["image"] == item["image"]:
                    shots[-1]["items"].append(item)
                    shots[-1]["duration"] += item["duration"]
                else:
                    shots.append({"image": item["image"], "items": [item], "duration": item["duration"]})

            mini_clips = []
            for shot in shots:
                shot_offset = 0.0
                zoom_range = shot_zoom_range(shot["duration"])
                for item in shot["items"]:
                    beat, i, beat_dur = item["beat"], item["index"], item["duration"]
                    shot_progress = (shot_offset / shot["duration"], (shot_offset + beat_dur) / shot["duration"])
                    shot_offset += beat_dur

                    beat_end_global = global_time + beat_dur
                    punch_offset = None
                    if global_time <= next_punch_at < beat_end_global and (beat_end_global - next_punch_at) > 0.03:
                        punch_offset = next_punch_at - global_time
                        next_punch_at += EFFECT_INTERVAL

                    intro_text = None
                    if seg.get("kind") == "product" and i == 0 and product_info and product_info.get("reason_selected"):
                        intro_text = product_info["reason_selected"]

                    if item.get("video"):
                        mini_clips.append(
                            _video_beat_clip(item["video"], beat_dur, canvas, beat["text"], font, product_info,
                                              highlight_text=beat.get("highlight"), intro_text=intro_text)
                        )
                    else:
                        mini_clips.extend(
                            _beat_clip(item["image"], beat["text"], canvas, font, product_info, beat_dur,
                                       shot_progress=shot_progress, zoom_range=zoom_range,
                                       punch_offset=punch_offset,
                                       highlight_text=beat.get("highlight"), intro_text=intro_text)
                        )
                    global_time = beat_end_global

            if not mini_clips:
                mini_clips = _beat_clip(
                    fallback_images[0] if fallback_images else None,
                    seg.get("text", ""), canvas, font, product_info, duration,
                )
                global_time += duration

            # Cắt cứng giữa các beat (method="chain", mặc định) — đã đo thực tế
            # crossfade (method="compose") chậm ~4.7 lần do phải blend alpha
            # từng frame ở độ phân giải HD, không khả thi với hàng chục beat.
            all_clips.extend(mini_clips)
            visual = mini_clips[0] if len(mini_clips) == 1 else concatenate_videoclips(mini_clips)
            all_clips.append(visual)

            segment_clip = visual.with_audio(audio_clip)
            all_clips.append(segment_clip)
            segment_clips.append(segment_clip)

        if not segment_clips:
            raise ValueError("Không có đoạn nào có audio để ghép video.")

        final = concatenate_videoclips(segment_clips)
        out_path = run_dir / out_name
        # Ghi ra file tạm rồi mới đổi tên — nếu bị ngắt giữa chừng thì
        # final_video.mp4 cũ (nếu có) vẫn nguyên vẹn, không bị file dở dang đè lên.
        tmp_path = out_path.with_name(out_path.stem + ".part.mp4")
        final.write_videofile(
            str(tmp_path), fps=fps, codec="libx264", audio_codec="aac", threads=4,
            logger=_EncodeProgressLogger(progress_cb) if progress_cb else None,
        )
        final.close()
        tmp_path.replace(out_path)
    finally:
        for c in all_clips:
            try:
                c.close()
            except Exception:
                pass

    return out_path
