"""Xuất run thành 1 DRAFT CapCut (project mở được ngay trong CapCut desktop)
thay vì render MP4 — để biên tập tiếp bằng hiệu ứng/chuyển cảnh/template có
sẵn của CapCut rồi Export ở đó.

CapCut không có API chính thức. Draft là thư mục JSON không mã hóa trong
%LOCALAPPDATA%/CapCut/User Data/Projects/com.lveditor.draft (đã kiểm tra với
CapCut 9.3, new_version 183.0.0). Thay vì tự viết JSON từ đầu (hàng trăm
trường không tài liệu), module này deep-copy các "khuôn" lấy từ chính draft
CapCut thật — src/capcut_templates/capcut_templates.json — rồi chỉ thay id,
đường dẫn, thời gian, chữ. CapCut cập nhật đổi định dạng thì lấy lại khuôn từ
1 draft mới tạo bằng CapCut bản đó.

Dựng giống cách chia beat/shot của src/video_assembly.py (cùng ảnh theo từng
câu, cùng chỗ chèn video thật), nhưng KHÔNG zoom/flash — phần đó để CapCut lo.

Các track:
- video: ảnh/video sản phẩm theo từng shot (video thật tắt tiếng)
- text: phụ đề từng câu, chữ nhấn mạnh, badge TOP, badge giá, card lý do chọn
- audio: giọng đọc từng đoạn nối liền
"""
import copy
import json
import os
import shutil
import subprocess
import time
import unicodedata
import uuid
from pathlib import Path
from typing import List, Optional

from moviepy import AudioFileClip, VideoFileClip
from PIL import Image

from .capcut_style import DEFAULT_KEN_BURNS
from .product_links import find_product_media, list_all_product_images
from .utils import format_price, match_by_name, slugify
from .video_assembly import _VIDEO_BEAT_MIN_DURATION, _build_beats, _collect_montage_images, _pick_fallback_image

_TEMPLATES_PATH = Path(__file__).parent / "capcut_templates" / "capcut_templates.json"
_US = 1_000_000  # CapCut tính thời gian bằng micro giây
_PHOTO_DURATION_US = 10_800_000_000  # ảnh tĩnh trong CapCut luôn có duration 3 tiếng
_CAPCUT_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}  # .webp đổi sang .png khi copy cho chắc

# Vị trí chữ: toạ độ CapCut chuẩn hoá -1..1 tính từ tâm khung, y dương = lên trên.
SUBTITLE_STYLE = {"size": 7.0, "y": -0.78, "color": (1.0, 1.0, 1.0), "stroke": (0.0, 0.0, 0.0)}
HIGHLIGHT_STYLE = {"size": 10.0, "y": -0.50, "color": (1.0, 0.80, 0.16), "stroke": (0.04, 0.04, 0.04)}
INTRO_STYLE = {"size": 6.0, "y": 0.62, "color": (1.0, 1.0, 1.0), "bg": "#2f6f5e"}
TOP_BADGE_STYLE = {"size": 9.0, "x": -0.82, "y": 0.84, "color": (1.0, 1.0, 1.0), "bg": "#e6a35f"}
PRICE_BADGE_STYLE = {"size": 9.0, "x": 0.80, "y": 0.84, "color": (1.0, 1.0, 1.0), "bg": "#2f6f5e"}


def default_drafts_root() -> Optional[Path]:
    """Thư mục draft CapCut desktop trên Windows; đặt CAPCUT_DRAFT_DIR trong .env
    nếu CapCut lưu project ở chỗ khác (Settings > Draft location)."""
    env = os.getenv("CAPCUT_DRAFT_DIR")
    if env:
        return Path(env)
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    path = Path(local) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return path if path.exists() else None


def capcut_is_running() -> bool:
    """CapCut đang mở có thể ghi đè root_meta_info.json lúc thoát -> draft mới
    không hiện trong danh sách. Chỉ để cảnh báo, không chặn."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe"], capture_output=True, text=True,
                             timeout=10).stdout
        return "CapCut.exe" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _us(seconds: float) -> int:
    return int(round(seconds * _US))


def _system_font_path() -> str:
    """Font mặc định của CapCut (en.ttf có đủ dấu tiếng Việt), lấy ở bản CapCut
    mới nhất đã cài — đường dẫn nằm trong thư mục theo số phiên bản."""
    local = os.getenv("LOCALAPPDATA", "")
    apps = Path(local) / "CapCut" / "Apps"
    candidates = sorted(apps.glob("*/Resources/Font/SystemFont/en.ttf"),
                        key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parts[-5].split(".")])
    return candidates[-1].as_posix() if candidates else ""


def _probe_media(path: Path) -> dict:
    if path.suffix.lower() in _CAPCUT_IMAGE_EXTS:
        with Image.open(path) as img:
            return {"width": img.width, "height": img.height}
    clip = VideoFileClip(str(path))
    try:
        return {"width": int(clip.w), "height": int(clip.h), "duration": float(clip.duration)}
    finally:
        clip.close()


def _audio_duration(path: Path) -> float:
    clip = AudioFileClip(str(path))
    try:
        return float(clip.duration)
    finally:
        clip.close()


def _hex(rgb: tuple) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


class _DraftBuilder:
    """Gom materials + tracks rồi ghi ra thư mục draft. Mỗi segment có material
    riêng (CapCut cũng làm vậy khi kéo cùng 1 file vào timeline nhiều lần)."""

    def __init__(self, canvas: tuple, media_dir: Path):
        self.tpl = json.loads(_TEMPLATES_PATH.read_text(encoding="utf-8"))
        self.canvas = canvas
        self.media_dir = media_dir
        self.materials = {k: [] for k in self.tpl["draft_content"]["materials"]
                          if isinstance(self.tpl["draft_content"]["materials"][k], list)}
        self.tracks: List[dict] = []
        self.font_path = _system_font_path()
        self._copied = {}
        self._text_render_index = 14000

    # ------------------------------------------------------------ media --
    def _import_file(self, src: Path) -> Path:
        """Copy media vào thư mục draft — xoá run sau này draft vẫn mở được."""
        if src in self._copied:
            return self._copied[src]
        self.media_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{len(self._copied):03d}_{slugify(src.stem, 40)}"
        if src.suffix.lower() == ".webp":
            dst = self.media_dir / f"{stem}.png"
            with Image.open(src) as img:
                img.save(dst)
        else:
            dst = self.media_dir / f"{stem}{src.suffix.lower()}"
            shutil.copy2(src, dst)
        self._copied[src] = dst
        return dst

    def _extras(self, kinds: List[str]) -> List[str]:
        ids = []
        for kind in kinds:
            item = copy.deepcopy(self.tpl["extras"][kind])
            item["id"] = _new_id()
            self.materials[kind].append(item)
            ids.append(item["id"])
        return ids

    def new_track(self, track_type: str) -> dict:
        track = copy.deepcopy(self.tpl["track"])
        track.update({"id": _new_id(), "type": track_type, "flag": 0, "attribute": 0, "segments": []})
        self.tracks.append(track)
        return track

    def add_media(self, track: dict, src: Path, start: float, duration: float, source_start: float = 0.0,
                  background: Optional[dict] = None):
        """`background` = {"scale", "alpha"} của bộ phong cách -> clip này là
        lớp nền phóng to phủ kín khung (ít nhất đủ cover theo tỉ lệ ảnh)."""
        path = self._import_file(src)
        info = _probe_media(path)
        is_photo = path.suffix.lower() in _CAPCUT_IMAGE_EXTS
        mat = copy.deepcopy(self.tpl["photo_material" if is_photo else "video_material"])
        mat.update({
            "id": _new_id(), "path": path.as_posix(), "material_name": path.name,
            "width": info["width"], "height": info["height"],
            "duration": _PHOTO_DURATION_US if is_photo else _us(info["duration"]),
        })
        self.materials["videos"].append(mat)

        seg = copy.deepcopy(self.tpl["video_segment"])
        seg.update({
            "id": _new_id(), "material_id": mat["id"],
            "source_timerange": {"start": _us(source_start), "duration": _us(duration)},
            "target_timerange": {"start": _us(start), "duration": _us(duration)},
            "volume": 0.0 if not is_photo else 1.0,  # video sản phẩm tắt tiếng, giọng đọc ở track audio
            "group_id": "", "render_index": 0, "track_render_index": 0, "keyframe_refs": [],
            "extra_material_refs": self._extras([
                "speeds", "placeholder_infos", "canvases", "material_animations",
                "sound_channel_mappings", "material_colors", "vocal_separations",
            ]),
        })
        scale, alpha = 1.0, 1.0
        if background:
            # CapCut scale 1.0 = vừa khít trong khung; cover = phủ kín theo cạnh còn lại.
            W, H = self.canvas
            fit = min(W / info["width"], H / info["height"])
            cover = max(W / info["width"], H / info["height"]) / fit
            scale, alpha = max(background["scale"], cover), background["alpha"]
            seg["volume"] = 0.0
        seg["clip"]["scale"] = {"x": scale, "y": scale}
        seg["clip"]["transform"] = {"x": 0.0, "y": 0.0}
        seg["clip"]["alpha"] = alpha
        track["segments"].append(seg)

    def add_transition(self, segment: dict, template: dict, next_duration_us: int) -> None:
        """Chuyển cảnh gắn vào clip ĐỨNG TRƯỚC điểm cắt (cách CapCut lưu)."""
        tr = copy.deepcopy(template)
        tr["id"] = _new_id()
        limit = min(segment["target_timerange"]["duration"], next_duration_us) // 2
        tr["duration"] = int(min(tr.get("duration", 1_000_000), limit))
        self.materials["transitions"].append(tr)
        segment["extra_material_refs"].append(tr["id"])

    def add_text_sample(self, track: dict, text: str, start: float, duration: float, sample: dict):
        """Chữ theo mẫu học từ project chỉnh tay (src/capcut_style.py): giữ
        nguyên font/màu/viền/nền/vị trí/hiệu ứng, chỉ thay nội dung."""
        text = unicodedata.normalize("NFC", text.strip())
        if not text or duration <= 0:
            return
        length = len(text.encode("utf-16-le")) // 2
        mat = copy.deepcopy(sample["material"])
        mat["id"] = _new_id()
        mat["content"] = json.dumps({"text": text, "styles": [dict(sample["style"], range=[0, length])]},
                                    ensure_ascii=False)
        self.materials["texts"].append(mat)

        refs = self._extras(["material_animations"])
        for effect in sample.get("effects", []):
            fx = copy.deepcopy(effect)
            fx["id"] = _new_id()
            self.materials["effects"].append(fx)
            refs.append(fx["id"])

        seg = copy.deepcopy(self.tpl["text_segment"])
        seg.update({
            "id": _new_id(), "material_id": mat["id"], "source_timerange": None,
            "target_timerange": {"start": _us(start), "duration": _us(duration)},
            "render_index": self._text_render_index, "keyframe_refs": [], "extra_material_refs": refs,
        })
        seg["clip"] = copy.deepcopy(sample["clip"])
        track["segments"].append(seg)

    def add_audio(self, track: dict, src: Path, start: float, duration: float):
        path = self._import_file(src)
        mat = copy.deepcopy(self.tpl["audio_material"])
        mat.update({"id": _new_id(), "name": path.name, "path": path.as_posix(), "duration": _us(duration)})
        self.materials["audios"].append(mat)

        seg = copy.deepcopy(self.tpl["audio_segment"])
        seg.update({
            "id": _new_id(), "material_id": mat["id"], "volume": 1.0,
            "source_timerange": {"start": 0, "duration": _us(duration)},
            "target_timerange": {"start": _us(start), "duration": _us(duration)},
            "extra_material_refs": self._extras([
                "speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations",
            ]),
        })
        track["segments"].append(seg)

    def add_text(self, track: dict, text: str, start: float, duration: float, style: dict):
        text = unicodedata.normalize("NFC", text.strip())
        if not text or duration <= 0:
            return
        length = len(text.encode("utf-16-le")) // 2  # range tính theo đơn vị UTF-16
        text_style = {
            "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                               "solid": {"alpha": 1.0, "color": list(style["color"])}}},
            "font": {"id": "", "path": self.font_path},
            "size": style["size"], "bold": True, "useLetterColor": True, "range": [0, length],
        }
        if style.get("stroke"):
            text_style["strokes"] = [{"alpha": 1.0, "width": 0.08, "content": {
                "render_type": "solid", "solid": {"alpha": 1.0, "color": list(style["stroke"])}}}]

        mat = copy.deepcopy(self.tpl["text_material"])
        mat.update({
            "id": _new_id(), "content": json.dumps({"text": text, "styles": [text_style]}, ensure_ascii=False),
            "font_size": style["size"], "font_path": self.font_path, "fonts": [],
            "text_color": _hex(style["color"]),
            "border_color": _hex(style["stroke"]) if style.get("stroke") else "",
            "border_width": 0.08 if style.get("stroke") else 0.0,
            "line_max_width": 0.82,
        })
        if style.get("bg"):
            mat.update({"background_style": 1, "background_color": style["bg"], "background_alpha": 0.92,
                        "background_round_radius": 0.4, "background_width": 0.28, "background_height": 0.28})
        else:
            mat.update({"background_style": 0, "background_alpha": 1.0})
        self.materials["texts"].append(mat)

        seg = copy.deepcopy(self.tpl["text_segment"])
        seg.update({
            "id": _new_id(), "material_id": mat["id"], "source_timerange": None,
            "target_timerange": {"start": _us(start), "duration": _us(duration)},
            "render_index": self._text_render_index, "keyframe_refs": [],
            "extra_material_refs": self._extras(["material_animations"]),
        })
        seg["clip"]["scale"] = {"x": 1.0, "y": 1.0}
        seg["clip"]["transform"] = {"x": style.get("x", 0.0), "y": style.get("y", 0.0)}
        track["segments"].append(seg)

    # ------------------------------------------------------------ output --
    def finalize_tracks(self):
        """Bỏ track rỗng, đánh render index theo thứ tự track (video dưới cùng,
        chữ đè lên trên) như CapCut tự làm."""
        self.tracks = [t for t in self.tracks if t["segments"]]
        render_index = 14000
        for tri, track in enumerate(self.tracks):
            for seg in track["segments"]:
                seg["track_render_index"] = tri
                if track["type"] == "text":
                    seg["render_index"] = render_index
            if track["type"] == "text":
                render_index += 1

    def draft_content(self, timeline_id: str, duration_us: int) -> dict:
        content = copy.deepcopy(self.tpl["draft_content"])
        W, H = self.canvas
        content.update({"id": timeline_id, "duration": duration_us, "tracks": self.tracks})
        content["canvas_config"].update({"width": W, "height": H, "ratio": "original"})
        for kind, items in self.materials.items():
            content["materials"][kind] = items
        return content


# Zoom-in + trượt ảnh qua lại (Ken Burns) bằng keyframe CapCut. Tên thuộc tính
# (KFTypeScaleX/Y, KFTypePositionX/Y) và cấu trúc common_keyframes lấy từ
# chính videoeditor.dll của CapCut 9.3. Toạ độ vị trí cùng đơn vị với
# clip.transform (1.0 = nửa khung hình), time_offset tính từ đầu clip.
# Thông số mặc định: DEFAULT_KEN_BURNS trong src/capcut_style.py (zoom_per_sec
# = mức phóng thêm mỗi giây, zoom_range = chặn dưới/trên của 1 clip, tương tự
# cho pan = trượt ngang; video_factor < 1 vì video thật đã có chuyển động).


def _keyframe_list(prop: str, points: List[tuple]) -> dict:
    return {
        "id": _new_id(), "material_id": "", "property_type": prop,
        "keyframe_list": [
            {"id": _new_id(), "curveType": "Line", "graphID": "", "time_offset": int(t),
             "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0}, "values": [float(v)]}
            for t, v in points
        ],
    }


def _ken_burns(segment: dict, index: int, is_video: bool, params: dict) -> None:
    """Phóng to dần suốt clip, đồng thời trượt ngang — clip chẵn trượt trái
    sang phải, clip lẻ ngược lại, kèm trượt dọc nhẹ để không đều đều."""
    dur_us = segment["target_timerange"]["duration"]
    dur = dur_us / _US
    clip = segment["clip"]
    base_scale = clip["scale"]["x"]
    x0, y0 = clip["transform"]["x"], clip["transform"]["y"]
    factor = params["video_factor"] if is_video else 1.0
    zoom = min(max(params["zoom_per_sec"] * dur, params["zoom_range"][0]), params["zoom_range"][1]) * factor
    pan = min(max(params["pan_per_sec"] * dur, params["pan_range"][0]), params["pan_range"][1]) * factor
    direction = 1 if index % 2 == 0 else -1
    s0, s1 = base_scale, base_scale * (1 + zoom)
    px0, px1 = x0 - direction * pan / 2, x0 + direction * pan / 2
    py0, py1 = y0, y0 + (pan / 4 if index % 3 == 0 else -pan / 4)
    end = max(dur_us - 1, 0)
    clip["scale"] = {"x": s0, "y": s0}
    clip["transform"] = {"x": px0, "y": py0}
    segment["common_keyframes"] = [
        _keyframe_list("KFTypeScaleX", [(0, s0), (end, s1)]),
        _keyframe_list("KFTypeScaleY", [(0, s0), (end, s1)]),
        _keyframe_list("KFTypePositionX", [(0, px0), (end, px1)]),
        _keyframe_list("KFTypePositionY", [(0, py0), (end, py1)]),
    ]


def apply_ken_burns(content: dict, start: float = 0.0, end: Optional[float] = None,
                    track_indexes: Optional[List[int]] = None, params: Optional[dict] = None) -> int:
    """Gắn zoom+trượt cho clip ảnh/video có điểm bắt đầu trong [start, end) giây.
    Mặc định chỉ áp cho track video TRÊN CÙNG (lớp ảnh chính, scale ~1) — lớp
    nền phóng to/mờ phía dưới giữ nguyên. Trả về số clip đã gắn."""
    params = {**DEFAULT_KEN_BURNS, **(params or {})}
    videos = {m["id"]: m for m in content["materials"]["videos"]}
    video_tracks = [i for i, t in enumerate(content["tracks"]) if t["type"] == "video"]
    if track_indexes is None:
        track_indexes = video_tracks[-1:]
    count = 0
    for ti in track_indexes:
        for i, seg in enumerate(content["tracks"][ti]["segments"]):
            seg_start = seg["target_timerange"]["start"] / _US
            if seg_start < start or (end is not None and seg_start >= end) or not seg.get("clip"):
                continue
            mat = videos.get(seg["material_id"])
            _ken_burns(seg, i, is_video=bool(mat and mat.get("type") == "video"), params=params)
            count += 1
    return count


def update_draft_content(draft_dir: Path, edit) -> None:
    """Sửa draft_content.json của 1 draft CÓ SẴN (vd đã chỉnh tay trong CapCut):
    `edit(content)` sửa tại chỗ. Ghi đồng bộ cả bản ở Timelines/<id>/ mà
    CapCut 9.x thực sự mở, và sao lưu bản cũ trước khi ghi."""
    if (draft_dir / ".locked").exists() and capcut_is_running():
        raise RuntimeError("CapCut đang mở project này — đóng CapCut rồi chạy lại để không bị ghi đè.")
    main = draft_dir / "draft_content.json"
    content = json.loads(main.read_text(encoding="utf-8"))
    backup = draft_dir / f"draft_content.before-auto-{time.strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copy2(main, backup)
    edit(content)
    targets = [main, draft_dir / "Timelines" / content["id"] / "draft_content.json"]
    for path in targets:
        if path.exists() or path == main:
            _write_json(path, content)
        for extra in (path.with_name("template-2.tmp"), path.with_name("draft_content.json.bak")):
            if extra.exists():
                _write_json(extra, content)


def _put_text(builder: "_DraftBuilder", texts_style: dict, role: str, track: dict, text: str,
              start: float, duration: float, default_style: dict) -> None:
    """Chữ theo bộ phong cách nếu có mẫu cho vai trò này, tắt nếu bộ phong
    cách đã bỏ vai trò đó, còn lại dùng kiểu mặc định."""
    sample = texts_style.get(role)
    if sample is None:
        builder.add_text(track, text, start, duration, default_style)
    elif sample.get("enabled", True) and sample.get("material"):
        builder.add_text_sample(track, text, start, duration, sample)


def _split_caption(text: str, start: float, duration: float, max_words: int) -> list:
    """Chia 1 câu dài thành các đoạn <= max_words từ (giống caption CapCut
    hiện từng cụm ngắn), thời gian chia theo số từ. max_words=0: giữ cả câu."""
    words = text.split()
    if max_words <= 0 or len(words) <= max_words:
        return [(text, start, duration)]
    n = -(-len(words) // max_words)
    size = -(-len(words) // n)  # chia đều, tránh đoạn cuối chỉ còn 1-2 từ
    chunks = [words[i:i + size] for i in range(0, len(words), size)]
    out, t = [], start
    for chunk in chunks:
        d = duration * len(chunk) / len(words)
        out.append((" ".join(chunk), t, d))
        t += d
    return out


def _add_transitions(builder: "_DraftBuilder", tracks: list, pool: list) -> None:
    """Chuyển cảnh ở mọi điểm cắt giữa 2 clip liền nhau, xoay vòng theo danh
    sách của bộ phong cách; lớp nền và lớp chính dùng CÙNG 1 kiểu tại 1 điểm cắt."""
    for track in tracks:
        segs = track["segments"]
        for i in range(len(segs) - 1):
            a, b = segs[i], segs[i + 1]
            gap = b["target_timerange"]["start"] - (a["target_timerange"]["start"] + a["target_timerange"]["duration"])
            if abs(gap) > 2000:  # chỉ nối clip liền nhau (lệch làm tròn < 2ms)
                continue
            builder.add_transition(a, pool[i % len(pool)], b["target_timerange"]["duration"])


def _unique_draft_dir(root: Path, name: str) -> Path:
    candidate, i = root / name, 1
    while candidate.exists():
        candidate = root / f"{name} ({i})"
        i += 1
    return candidate


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _register_in_root_meta(root: Path, meta: dict, draft_dir: Path) -> None:
    """Thêm draft vào danh sách CapCut hiển thị ở màn hình chính. Lưu bản sao
    root_meta_info.json trước khi sửa."""
    root_meta = root / "root_meta_info.json"
    if not root_meta.exists():
        return  # CapCut tự quét thư mục khi mở
    data = json.loads(root_meta.read_text(encoding="utf-8"))
    shutil.copy2(root_meta, root_meta.with_name("root_meta_info.json.auto-video.bak"))
    entry = {
        k: meta[k] for k in meta
        if k in {"draft_cover", "draft_fold_path", "draft_id", "draft_name", "draft_root_path",
                 "tm_draft_create", "tm_draft_modified", "tm_duration", "draft_type", "draft_new_version",
                 "draft_is_ai_shorts", "draft_is_invisible", "draft_is_cloud_temp_draft", "cloud_draft_sync",
                 "cloud_draft_cover", "tm_draft_removed", "tm_draft_cloud_entry_id", "tm_draft_cloud_space_id",
                 "tm_draft_cloud_user_id", "tm_draft_cloud_parent_entry_id", "tm_draft_cloud_modified"}
    }
    entry["draft_json_file"] = (draft_dir / "draft_content.json").as_posix()
    entry["draft_timeline_materials_size"] = meta.get("draft_timeline_materials_size_", 0)
    entry["streaming_edit_draft_ready"] = True
    data["all_draft_store"] = [e for e in data["all_draft_store"]
                               if Path(e.get("draft_fold_path", "")) != draft_dir]  # xuất lại cùng tên
    data["all_draft_store"].insert(0, entry)
    data["draft_ids"] = len(data["all_draft_store"])
    _write_json(root_meta, data)


def export_capcut_draft(
    run_dir: Path,
    canvas: tuple = (1920, 1080),
    draft_name: Optional[str] = None,
    drafts_root: Optional[Path] = None,
    style: Optional[dict] = None,
) -> Path:
    """Tạo draft CapCut từ run đã có 06_media_manifest.json. Trả về thư mục draft.
    `style` = bộ phong cách (src/capcut_style.py: load_style); bỏ trống thì
    dùng kiểu mặc định (chữ *_STYLE ở đầu file, có zoom, không chuyển cảnh)."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "06_media_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Không tìm thấy {manifest_path} — chạy bước Script & Audio trước.")
    root = Path(drafts_root) if drafts_root else default_drafts_root()
    if not root or not root.exists():
        raise FileNotFoundError(
            "Không tìm thấy thư mục draft CapCut. Mở CapCut 1 lần, hoặc đặt CAPCUT_DRAFT_DIR trong .env "
            "(xem CapCut > Settings > Draft location)."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    products_path = run_dir / "03_products_selected.json"
    products_data = json.loads(products_path.read_text(encoding="utf-8")) if products_path.exists() else {}
    products_by_name = {p["name"]: p for p in products_data.get("selected_products", [])}
    montage_images = _collect_montage_images(products_data, run_dir)

    draft_dir = _unique_draft_dir(root, draft_name or f"AutoVideo {run_dir.name}")
    builder = _DraftBuilder(canvas, draft_dir / "Resources" / "auto_video")
    style = style or {}
    texts_style = style.get("texts", {})
    bg_style = style.get("background_layer", {})
    t_bg = builder.new_track("video") if bg_style.get("enabled") else None
    t_video = builder.new_track("video")
    t_intro = builder.new_track("text")
    t_top = builder.new_track("text")
    t_price = builder.new_track("text")
    t_highlight = builder.new_track("text")
    t_sub = builder.new_track("text")
    t_audio = builder.new_track("audio")

    timeline = 0.0
    try:
        for seg in manifest:
            if not seg.get("audio_file"):
                continue
            audio_path = run_dir / seg["audio_file"]
            duration = _audio_duration(audio_path)
            builder.add_audio(t_audio, audio_path, timeline, duration)

            product_info, fallback_images = None, []
            if seg.get("kind") == "product":
                product_info = match_by_name(seg.get("product_name") or "", products_by_name)
                if product_info:
                    fallback_images = list_all_product_images(product_info, run_dir)
                if not fallback_images and seg.get("image_file"):
                    fallback_images = [run_dir / seg["image_file"]]
            elif montage_images:
                fallback_images = montage_images

            beats = _build_beats(seg, duration)
            resolved = []
            for i, beat in enumerate(beats):
                beat_dur = beat["end"] - beat["start"]
                if beat_dur <= 0.01:
                    continue
                img = (run_dir / beat["image"]) if beat.get("image") else _pick_fallback_image(fallback_images, i, len(beats))
                resolved.append({"beat": beat, "index": i, "duration": beat_dur, "image": img})

            # Cùng quy tắc với video_assembly: video thật thay câu dài nhất.
            if seg.get("kind") == "product" and product_info and resolved:
                _, video_path = find_product_media(product_info, run_dir)
                if video_path:
                    try:
                        video_duration = _probe_media(video_path)["duration"]
                    except Exception as e:
                        print(f"[CẢNH BÁO] Không đọc được video '{video_path.name}': {e} — dùng ảnh tĩnh.")
                        video_duration = 0.0
                    longest = max(resolved, key=lambda r: r["duration"])
                    if longest["duration"] >= _VIDEO_BEAT_MIN_DURATION and video_duration >= longest["duration"] + 0.2:
                        longest["video"] = video_path
                        longest["video_start"] = max(0.0, (video_duration - longest["duration"]) / 2)
                        longest["image"] = object()

            # Gộp beat liên tiếp cùng ảnh thành 1 clip — dễ kéo/thay ảnh trong CapCut.
            shots = []
            for item in resolved:
                if shots and not item.get("video") and not shots[-1].get("video") and shots[-1]["image"] == item["image"]:
                    shots[-1]["duration"] += item["duration"]
                else:
                    shots.append(dict(item))
            offset = timeline
            for shot in shots:
                src = shot.get("video") or shot["image"]
                if isinstance(src, Path) and src.exists():
                    if t_bg is not None:
                        builder.add_media(t_bg, src, offset, shot["duration"], shot.get("video_start", 0.0),
                                          background=bg_style)
                    builder.add_media(t_video, src, offset, shot["duration"], shot.get("video_start", 0.0))
                offset += shot["duration"]

            for item in resolved:
                beat = item["beat"]
                start = timeline + beat["start"]
                max_words = texts_style.get("subtitle", {}).get("max_words", 0)
                for chunk, c_start, c_dur in _split_caption(beat["text"], start, item["duration"], max_words):
                    _put_text(builder, texts_style, "subtitle", t_sub, chunk, c_start, c_dur, SUBTITLE_STYLE)
                if beat.get("highlight"):
                    _put_text(builder, texts_style, "highlight", t_highlight, beat["highlight"], start,
                              item["duration"], HIGHLIGHT_STYLE)
                if seg.get("kind") == "product" and item["index"] == 0 and product_info and product_info.get("reason_selected"):
                    _put_text(builder, texts_style, "intro", t_intro, product_info["reason_selected"], start,
                              item["duration"], INTRO_STYLE)

            if product_info:
                if product_info.get("rank"):
                    _put_text(builder, texts_style, "top_badge", t_top, f"TOP {product_info['rank']}", timeline,
                              duration, TOP_BADGE_STYLE)
                if product_info.get("price") is not None:
                    _put_text(builder, texts_style, "price_badge", t_price, format_price(product_info["price"]),
                              timeline, duration, PRICE_BADGE_STYLE)

            timeline += duration

        if timeline <= 0:
            raise ValueError("Không có đoạn nào có audio để xuất draft.")

        pool = style.get("transitions", {}).get("pool", []) if style.get("transitions", {}).get("enabled") else []
        if pool:
            _add_transitions(builder, [t for t in (t_bg, t_video) if t is not None], pool)
        builder.finalize_tracks()
        duration_us = _us(timeline)
        project_id, timeline_id = _new_id(), _new_id()
        content = builder.draft_content(timeline_id, duration_us)
        kb = style.get("ken_burns", DEFAULT_KEN_BURNS)
        if kb.get("enabled", True):
            apply_ken_burns(content, track_indexes=[content["tracks"].index(t_video)], params=kb)
        _write_json(draft_dir / "draft_content.json", content)
        _write_json(draft_dir / "Timelines" / timeline_id / "draft_content.json", content)
        now_us = int(time.time() * _US)
        _write_json(draft_dir / "Timelines" / "project.json", {
            "config": {"color_space": -1, "mixed_track_mode_on": False, "render_index_track_mode_on": False,
                       "use_float_render": False},
            "create_time": now_us, "id": project_id, "main_timeline_id": timeline_id,
            "timelines": [{"create_time": now_us, "id": timeline_id, "is_marked_delete": False,
                           "name": "Timeline 01", "update_time": now_us}],
            "update_time": now_us, "version": 0,
        })
        _write_json(draft_dir / "timeline_layout.json", {
            "dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [timeline_id], "timelineNames": ["Timeline 01"]}],
            "layoutOrientation": 1,
        })
        for name in ("attachment_pc_common", "draft_agency_config", "performance_opt_info"):
            _write_json(draft_dir / f"{name}.json", builder.tpl[name])

        cover = next((p for p in builder._copied.values() if p.suffix.lower() in _CAPCUT_IMAGE_EXTS), None)
        if cover:
            with Image.open(cover) as img:
                img.convert("RGB").save(draft_dir / "draft_cover.jpg", quality=85)

        media_size = sum(p.stat().st_size for p in builder._copied.values())
        meta = copy.deepcopy(builder.tpl["draft_meta_info"])
        meta.update({
            "draft_fold_path": draft_dir.as_posix(), "draft_id": project_id, "draft_name": draft_dir.name,
            "draft_root_path": str(root), "draft_cover": "draft_cover.jpg",
            "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_duration": duration_us,
            "draft_timeline_materials_size_": media_size,
        })
        _write_json(draft_dir / "draft_meta_info.json", meta)
        secs = now_us // _US
        (draft_dir / "draft_settings").write_text(
            f"[General]\ndraft_create_time={secs}\ndraft_last_edit_time={secs}\nreal_edit_seconds=0\nreal_edit_keys=0\n",
            encoding="utf-8",
        )
        _register_in_root_meta(root, {**meta, "draft_cover": (draft_dir / "draft_cover.jpg").as_posix()}, draft_dir)
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)  # không để lại draft dở dang làm CapCut lỗi
        raise

    return draft_dir
