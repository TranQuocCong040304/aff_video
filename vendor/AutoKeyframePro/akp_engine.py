# -*- coding: utf-8 -*-
"""
AKP ENGINE - Auto Keyframe Pro V2
Add-on cho Tool AutoCapcut V6.3 (CapCut PC v7.8)

Xu ly truc tiep draft_content.json cua CapCut:
  1. Keyframe chuyen dong co ease-curve muot (bake S-curve, khong dung curveType la)
  2. Lop nen (background fill) tu chinh anh: cover-scale + alpha thap + canvas blur + zoom cham
  3. Khung hinh 19:6 kieu letterbox trong canvas 1920x1080
  4. Vignette + vien mo

Nguyen tac an toan:
  - Luon backup draft_content.json truoc khi ghi
  - Moi object JSON sinh ra deu CLONE tu object that co san trong draft
    => dam bao dung schema cua dung phien ban CapCut dang cai
  - Moi thu AKP tao ra deu duoc ghi ID vao akp_state.json => go sach khi chay lai
"""

import os
import json
import uuid
import math
import copy
import shutil
import datetime
import subprocess

# ----------------------------------------------------------------------------
# Hang so
# ----------------------------------------------------------------------------

STATE_FILE = "akp_state.json"
CONTENT_FILE = "draft_content.json"

# Cac kieu keyframe: 5 kieu goc cua tool V6.3 + 3 kieu Pro moi
KEYFRAME_STYLES = [
    "Không Sử Dụng",
    "Zoom In Từ Từ (100% → 115%)",
    "Zoom Out (120% → 100%)",
    "Pan Trái Sang Phải",
    "Pan Phải Sang Trái",
    "Ngẫu nhiên Zoom & Pan",
    "Ken Burns Pro (Zoom + Pan chéo)",
    "Parallax 19:6 (nền ngược chiều)",
    "Breathing Slow (rất nhẹ, cho talking head)",
]

RANDOM_POOL = [
    "Zoom In Từ Từ (100% → 115%)",
    "Zoom Out (120% → 100%)",
    "Pan Trái Sang Phải",
    "Pan Phải Sang Trái",
    "Ken Burns Pro (Zoom + Pan chéo)",
]

# Muc blur cua canvas CapCut (gia tri that CapCut ghi trong draft)
CANVAS_BLUR_LEVELS = {
    "Nhẹ": 0.0625,
    "Vừa": 0.375,
    "Mạnh": 0.75,
    "Tối đa": 1.0,
}


# ----------------------------------------------------------------------------
# Tien ich
# ----------------------------------------------------------------------------

def nid():
    """ID kieu CapCut: UUID viet hoa."""
    return str(uuid.uuid4()).upper()


CAPCUT_PROCESS_HINTS = ("capcut", "jianying", "lveditor")


def capcut_running():
    """
    CapCut co dang mo khong?

    RAT QUAN TRONG: CapCut giu toan bo du an trong bo nho. Neu no dang mo,
    chi vai giay sau khi tool ghi xong no se tu luu de len, xoa sach moi
    thu tool vua them - nhin vao log thi thay "thanh cong" nhung du an
    khong he thay doi.

    Tra ve True / False, hoac None neu khong kiem tra duoc (khong phai Windows).
    """
    if os.name != "nt":
        return None
    try:
        kwargs = {}
        si = getattr(subprocess, "STARTUPINFO", None)
        if si is not None:
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = info
        out = subprocess.check_output(["tasklist", "/FO", "CSV", "/NH"],
                                      stderr=subprocess.DEVNULL, **kwargs)
        if isinstance(out, bytes):
            out = out.decode("utf-8", "ignore")
    except Exception:
        return None
    low = out.lower()
    return any(h in low for h in CAPCUT_PROCESS_HINTS)


def verify_saved(folder, log=print):
    """
    Doc lai file vua ghi de chac chan no van con dau vet cua AKP.
    Neu bi ghi de (CapCut vua luu lai) thi bao ngay thay vi de nguoi dung
    tuong da thanh cong.
    """
    p = os.path.join(folder, CONTENT_FILE)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    for t in data.get("tracks", []):
        for s in t.get("segments", []) or []:
            if s.get("desc") in AKP_MARKS:
                return True
    log("   ! CẢNH BÁO: file vừa lưu đã bị ghi đè, không còn lớp nào của AKP.")
    log("     Gần như chắc chắn CapCut đang mở và vừa tự lưu đè lên.")
    log("     Hãy đóng hẳn CapCut (kể cả icon dưới khay hệ thống) rồi chạy lại.")
    return False


def smoothstep(t):
    """Ease In/Out co ban (S-curve). t in [0,1]."""
    return t * t * (3.0 - 2.0 * t)


def ease_out_cubic(t):
    return 1.0 - (1.0 - t) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ----------------------------------------------------------------------------
# Keyframe
# ----------------------------------------------------------------------------

def make_kf(time_offset, value):
    """1 diem keyframe - dung y het schema tool V6.3 / CapCut 7.8 ghi ra."""
    return {
        "id": nid(),
        "curveType": "Line",
        "time_offset": int(time_offset),
        "left_control": {"x": 0.0, "y": 0.0},
        "right_control": {"x": 0.0, "y": 0.0},
        "values": [round(float(value), 6)],
        "string_value": "",
        "graphID": "",
    }


def make_kf_group(property_type, entries):
    return {
        "id": nid(),
        "material_id": "",
        "property_type": property_type,
        "keyframe_list": entries,
    }


def build_track(property_type, duration_us, v_from, v_to, ease=True, points=9,
                easing=smoothstep):
    """
    Sinh 1 nhom keyframe di tu v_from -> v_to.

    Day la trai tim cua 'Curve muot': thay vi 2-4 diem tuyen tinh (curveType 'Line'
    => chuyen dong deu, khong co gia toc, nhin may moc), ta BAKE san duong cong
    ease-in/ease-out bang cach lay mau nhieu diem tren duong cong do.
    Cach nay khong phu thuoc vao enum curveType nao cua CapCut nen chay duoc
    tren moi phien ban.
    """
    if not ease or abs(v_to - v_from) < 1e-9:
        points = 2          # gia tri khong doi thi 2 diem la du
    points = max(2, int(points))
    entries = []
    for i in range(points):
        t = i / float(points - 1)
        te = easing(t) if ease else t
        entries.append(make_kf(int(round(duration_us * t)), lerp(v_from, v_to, te)))
    return make_kf_group(property_type, entries)


def keyframes_for_style(duration_us, style, index=0, zoom=1.15, pan=0.15,
                        ease=True, points=9, invert=False):
    """
    Tra ve (danh_sach_nhom_keyframe, clip_meta).
    clip_meta la gia tri tinh cua clip (CapCut can khop voi keyframe dau tien).
    invert=True dung cho lop nen o che do Parallax (chay nguoc chieu lop chinh).
    """
    if not style or style == "Không Sử Dụng":
        return [], None

    if style == "Ngẫu nhiên Zoom & Pan":
        style = RANDOM_POOL[index % len(RANDOM_POOL)]

    sx = sy = px = py = None  # (from, to)

    if style == "Zoom In Từ Từ (100% → 115%)":
        sx = sy = (1.0, zoom)
        px = py = (0.0, 0.0)
    elif style == "Zoom Out (120% → 100%)":
        sx = sy = (zoom, 1.0)
        px = py = (0.0, 0.0)
    elif style == "Pan Trái Sang Phải":
        sx = sy = (zoom, zoom)
        px = (pan, -pan)
        py = (0.0, 0.0)
    elif style == "Pan Phải Sang Trái":
        sx = sy = (zoom, zoom)
        px = (-pan, pan)
        py = (0.0, 0.0)
    elif style == "Ken Burns Pro (Zoom + Pan chéo)":
        # Zoom nhe + troi cheo: kieu tai lieu/phong su, sang nhat trong 8 kieu
        d = [(1.0, zoom, pan * 0.6, -pan * 0.35, -pan * 0.25, pan * 0.2),
             (zoom, 1.0, -pan * 0.5, pan * 0.4, pan * 0.3, -pan * 0.15)][index % 2]
        sx = sy = (d[0], d[1])
        px = (d[2], d[3])
        py = (d[4], d[5])
    elif style == "Parallax 19:6 (nền ngược chiều)":
        # Lop chinh troi ngang cham; lop nen se duoc goi voi invert=True
        sx = sy = (zoom, zoom)
        px = (pan * 0.5, -pan * 0.5)
        py = (0.0, 0.0)
    elif style == "Breathing Slow (rất nhẹ, cho talking head)":
        z = 1.0 + (zoom - 1.0) * 0.35
        sx = sy = (1.0, z)
        px = py = (0.0, 0.0)
    else:
        return [], None

    if invert:
        px = (-px[0], -px[1])
        py = (-py[0], -py[1])

    groups = [
        build_track("KFTypeScaleX", duration_us, sx[0], sx[1], ease, points),
        build_track("KFTypeScaleY", duration_us, sy[0], sy[1], ease, points),
        build_track("KFTypePositionX", duration_us, px[0], px[1], ease, points),
        build_track("KFTypePositionY", duration_us, py[0], py[1], ease, points),
    ]
    clip_meta = {
        "scale_x": sx[0], "scale_y": sy[0],
        "transform_x": px[0], "transform_y": py[0],
        "uniform_scale_on": False, "uniform_scale_value": sx[0],
    }
    return groups, clip_meta


def apply_clip_meta(seg, clip_meta):
    if not clip_meta:
        return
    clip = seg.setdefault("clip", {})
    scale = clip.setdefault("scale", {})
    scale["x"] = clip_meta.get("scale_x", 1.0)
    scale["y"] = clip_meta.get("scale_y", 1.0)
    transform = clip.setdefault("transform", {})
    transform["x"] = clip_meta.get("transform_x", 0.0)
    transform["y"] = clip_meta.get("transform_y", 0.0)
    uni = seg.setdefault("uniform_scale", {})
    uni["on"] = clip_meta.get("uniform_scale_on", False)
    uni["value"] = clip_meta.get("uniform_scale_value", 1.0)
    seg["keyframe_refs"] = []


# ----------------------------------------------------------------------------
# Draft
# ----------------------------------------------------------------------------

class Draft(object):
    """Doc / ghi / backup mot thu muc draft CapCut."""

    def __init__(self, folder):
        self.folder = folder
        # CapCut 7.x: timeline THAT nam trong Timelines/<main_timeline_id>/,
        # con draft_content.json o thu muc goc chi la ban sao. Chi sua ban goc
        # thi khi mo du an CapCut doc tu Timelines/ va ghi de nguoc lai.
        self.timeline_dir = self._find_timeline_dir()
        self.path = os.path.join(self.timeline_dir or folder, CONTENT_FILE)
        if not os.path.exists(self.path):
            self.path = os.path.join(folder, CONTENT_FILE)
        if not os.path.exists(self.path):
            raise FileNotFoundError("Không tìm thấy %s trong %s" % (CONTENT_FILE, folder))
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = json.load(f)

        # moi ban sao giu 'id' rieng cua no, khong duoc tron lan
        self._ids = {}
        for d in self.target_dirs():
            p = os.path.join(d, CONTENT_FILE)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._ids[d] = json.load(f).get("id")
                except Exception:
                    pass
        self.state = self._load_state()

    def _find_timeline_dir(self):
        """Doc Timelines/project.json de biet timeline chinh nam o dau."""
        proj = os.path.join(self.folder, "Timelines", "project.json")
        if not os.path.exists(proj):
            return None
        try:
            with open(proj, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            return None
        tid = info.get("main_timeline_id")
        if not tid:
            tl = info.get("timelines") or []
            tid = tl[0].get("id") if tl else None
        if not tid:
            return None
        d = os.path.join(self.folder, "Timelines", tid)
        return d if os.path.isdir(d) else None

    def target_dirs(self):
        """Tat ca thu muc chua mot ban draft_content.json cua du an nay."""
        dirs = [self.folder]
        if self.timeline_dir and self.timeline_dir != self.folder:
            dirs.append(self.timeline_dir)
        return dirs

    # -- state (de go sach khi chay lai) --------------------------------------
    def _state_path(self):
        return os.path.join(self.folder, STATE_FILE)

    def _load_state(self):
        p = self._state_path()
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"tracks": [], "segments": [], "materials": [], "backups": []}

    def save_state(self):
        with open(self._state_path(), "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    # -- backup ---------------------------------------------------------------
    def original_path(self):
        return os.path.join(self.folder, "draft_content.AKP_original.json")

    def backup(self):
        # ban goc that su (truoc lan chay AKP dau tien) - khong bao gio ghi de.
        # Luu rieng cho TUNG thu muc chua draft (goc + Timelines/<id>).
        for d in self.target_dirs():
            src = os.path.join(d, CONTENT_FILE)
            dst = os.path.join(d, "draft_content.AKP_original.json")
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
        orig = self.original_path()
        if not os.path.exists(orig):
            shutil.copy2(self.path, orig)
        meta = os.path.join(self.folder, "draft_meta_info.json")
        meta_orig = os.path.join(self.folder, "draft_meta_info.AKP_original.json")
        if os.path.exists(meta) and not os.path.exists(meta_orig):
            shutil.copy2(meta, meta_orig)

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(self.folder, "draft_content.AKP_backup_%s.json" % stamp)
        n = 1
        while os.path.exists(dst):
            dst = os.path.join(self.folder,
                               "draft_content.AKP_backup_%s_%d.json" % (stamp, n))
            n += 1
        shutil.copy2(self.path, dst)
        self.state.setdefault("backups", []).append(os.path.basename(dst))
        # chi giu 5 ban gan nhat
        while len(self.state["backups"]) > 5:
            old = self.state["backups"].pop(0)
            try:
                os.remove(os.path.join(self.folder, old))
            except Exception:
                pass
        return dst

    def list_backups(self):
        out = [f for f in os.listdir(self.folder)
               if f.startswith("draft_content.AKP_backup_") and f.endswith(".json")]
        return sorted(out)

    def restore_latest(self):
        """Tra draft ve dung trang thai TRUOC lan chay AKP dau tien."""
        src = self.original_path()
        if not os.path.exists(src):
            bks = self.list_backups()
            if not bks:
                return None
            src = os.path.join(self.folder, bks[-1])
        # tra lai ban goc cho tung thu muc (goc + Timelines/<id>) va ca ban sao guong
        for d in self.target_dirs():
            per = os.path.join(d, "draft_content.AKP_original.json")
            use = per if os.path.exists(per) else src
            try:
                shutil.copy2(use, os.path.join(d, CONTENT_FILE))
            except Exception:
                pass
            for name in self.MIRRORS:
                orig = os.path.join(d, name + ".AKP_original")
                if os.path.exists(orig):
                    try:
                        shutil.copy2(orig, os.path.join(d, name))
                    except Exception:
                        pass
        meta_orig = os.path.join(self.folder, "draft_meta_info.AKP_original.json")
        if os.path.exists(meta_orig):
            shutil.copy2(meta_orig, os.path.join(self.folder, "draft_meta_info.json"))
        st = self._state_path()
        if os.path.exists(st):
            try:
                os.remove(st)
            except Exception:
                pass
        return src

    # CapCut khong chi doc draft_content.json. No con giu ban sao guong
    # template-2.tmp va draft_content.json.bak. Khi mo du an, neu 2 file nay
    # con noi dung cu thi CapCut khoi phuc theo chung va ghi de nguoc lai
    # draft_content.json -> nhin nhu tool "khong chay".
    MIRRORS = ("template-2.tmp", "draft_content.json.bak")

    def save(self):
        for d in self.target_dirs():
            data = self.data
            keep_id = self._ids.get(d)
            if keep_id and data.get("id") != keep_id:
                data = dict(self.data)
                data["id"] = keep_id          # moi ban giu id rieng
            main = os.path.join(d, CONTENT_FILE)
            with open(main, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            for name in self.MIRRORS:
                p = os.path.join(d, name)
                if not os.path.exists(p):
                    continue
                orig = os.path.join(d, name + ".AKP_original")
                if not os.path.exists(orig):
                    try:
                        shutil.copy2(p, orig)
                    except Exception:
                        pass
                try:
                    shutil.copy2(main, p)
                except Exception:
                    pass

        self.save_state()

    # -- truy van -------------------------------------------------------------
    @property
    def canvas(self):
        c = self.data.get("canvas_config") or {}
        return int(c.get("width") or 1920), int(c.get("height") or 1080)

    @property
    def duration(self):
        return int(self.data.get("duration") or 0)

    def video_tracks(self):
        return [t for t in self.data.get("tracks", []) if t.get("type") == "video"]

    def material_index(self):
        """map id -> (ten_category, object)"""
        idx = {}
        for cat, lst in self.data.get("materials", {}).items():
            if isinstance(lst, list):
                for obj in lst:
                    if isinstance(obj, dict) and obj.get("id"):
                        idx[obj["id"]] = (cat, obj)
        return idx

    def video_material(self, seg):
        for v in self.data.get("materials", {}).get("videos", []):
            if v.get("id") == seg.get("material_id"):
                return v
        return None

    def track_summary(self):
        """Mo ta tung track video de nguoi dung chon dung track anh chinh."""
        out = []
        vids = {v.get("id"): v for v in self.data.get("materials", {}).get("videos", [])}
        for i, t in enumerate(self.data.get("tracks", [])):
            if t.get("type") != "video":
                continue
            segs = t.get("segments", [])
            if not segs:
                continue
            n_photo = sum(1 for s in segs
                          if (vids.get(s.get("material_id")) or {}).get("type") == "photo")
            n_bg = sum(1 for s in segs if float(s.get("clip", {}).get("alpha", 1.0)) < 0.95)
            n_kf = sum(1 for s in segs if s.get("common_keyframes"))
            out.append({
                "index": i,
                "n": len(segs),
                "photos": n_photo,
                "bg_like": n_bg,
                "with_kf": n_kf,
                "label": "Track %d — %d clip (%d ảnh, %d lớp nền sẵn, %d đã có keyframe)"
                         % (i, len(segs), n_photo, n_bg, n_kf),
            })
        return out

    def guess_main_track(self):
        """Track anh chinh = nhieu anh alpha ~1.0 nhat."""
        best, best_score = None, -1
        vids = {v.get("id"): v for v in self.data.get("materials", {}).get("videos", [])}
        for i, t in enumerate(self.data.get("tracks", [])):
            if t.get("type") != "video":
                continue
            score = 0
            for s in t.get("segments", []):
                mat = vids.get(s.get("material_id")) or {}
                if mat.get("type") == "photo" and float(s.get("clip", {}).get("alpha", 1.0)) >= 0.95:
                    score += 1
            if score > best_score:
                best, best_score = i, score
        return best


# ----------------------------------------------------------------------------
# Clone material / segment an toan
# ----------------------------------------------------------------------------

def clone_material(draft, src_id, overrides=None):
    """Nhan ban 1 material bat ky (giu nguyen schema), doi ID."""
    idx = draft.material_index()
    if src_id not in idx:
        return None
    cat, obj = idx[src_id]
    new = copy.deepcopy(obj)
    new["id"] = nid()
    if overrides:
        new.update(overrides)
    draft.data["materials"].setdefault(cat, []).append(new)
    draft.state.setdefault("materials", []).append(new["id"])
    return new


def clone_segment(draft, seg, clone_refs=True):
    """
    Nhan ban 1 segment: ID moi, va nhan ban luon cac material phu
    (speed / canvas / mau / sound mapping...) de 2 clip khong dinh nhau.
    """
    new = copy.deepcopy(seg)
    new["id"] = nid()
    new["common_keyframes"] = []
    new["keyframe_refs"] = []
    if clone_refs:
        refs = []
        for rid in seg.get("extra_material_refs", []) or []:
            c = clone_material(draft, rid)
            refs.append(c["id"] if c else rid)
        new["extra_material_refs"] = refs
    draft.state.setdefault("segments", []).append(new["id"])
    return new


def set_canvas_blur(draft, seg, blur_value):
    """
    Bat 'Phong nen mo' (canvas blur) NATIVE cua CapCut cho 1 clip.
    Day la blur that (Gaussian) do chinh CapCut render, khong can effect_id nao.
    """
    idx = draft.material_index()
    for rid in seg.get("extra_material_refs", []) or []:
        if rid in idx and idx[rid][0] == "canvases":
            cv = idx[rid][1]
            cv["type"] = "canvas_blur"
            cv["blur"] = float(blur_value)
            cv["color"] = ""
            cv["image"] = ""
            return True
    return False


def new_video_track(draft, segments):
    """
    Tao 1 track video moi bang cach CLONE cau truc cua track video co san
    (giu dung bo truong + flag ma phien ban CapCut nay dung), chi thay id/segments.
    """
    tpl = None
    for t in draft.data.get("tracks", []):
        if t.get("type") == "video" and t.get("segments"):
            tpl = t
            break
    if tpl is not None:
        track = {k: copy.deepcopy(v) for k, v in tpl.items() if k != "segments"}
    else:
        track = {"type": "video", "attribute": 0, "flag": 0}
    track["id"] = nid()
    track["type"] = "video"
    track["segments"] = segments
    return track


def renumber_tracks(draft):
    """
    CapCut dat track_render_index = vi tri cua track trong mang 'tracks'.
    Sau khi chen them track, phai danh so lai het, neu khong se bi trung layer.
    """
    for i, t in enumerate(draft.data.get("tracks", [])):
        for s in t.get("segments", []) or []:
            if isinstance(s, dict) and "track_render_index" in s:
                s["track_render_index"] = i


def real_image_size(path):
    """
    Doc kich thuoc THAT cua file anh bang thuan Python (khong can Pillow).
    Can thiet vi Tool V6.3 ghi cung 1920x1080 cho MOI anh trong draft,
    neu tin theo do thi anh vuong se khong duoc phong to phu kin khung.
    """
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            head = f.read(32)
            # PNG
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w = int.from_bytes(head[16:20], "big")
                h = int.from_bytes(head[20:24], "big")
                return (w, h) if w and h else None
            # GIF
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return (int.from_bytes(head[6:8], "little"),
                        int.from_bytes(head[8:10], "little"))
            # WEBP (VP8X)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8X":
                    w = int.from_bytes(head[24:27], "little") + 1
                    h = int.from_bytes(head[27:30], "little") + 1
                    return (w, h)
            # JPEG
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        return None
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if not marker:
                        return None
                    m = marker[0]
                    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                        continue
                    size = int.from_bytes(f.read(2), "big")
                    if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        f.read(1)
                        h = int.from_bytes(f.read(2), "big")
                        w = int.from_bytes(f.read(2), "big")
                        return (w, h) if w and h else None
                    f.seek(size - 2, 1)
    except Exception:
        return None
    return None


def material_size(mat):
    """Uu tien kich thuoc that cua file, khong co thi lay trong draft."""
    real = real_image_size(mat.get("path"))
    if real:
        return real
    return mat.get("width"), mat.get("height")


def cover_scale(canvas_w, canvas_h, mat_w, mat_h):
    """
    He so scale de anh PHU KIN khung (CapCut mac dinh fit 'contain' o scale 1.0).
    Vi du anh vuong 1024 trong canvas 1920x1080 -> 1.7778 (dung bang so 1.78
    ma du an 0914 (2) dang dung).
    """
    if not mat_w or not mat_h:
        return float(canvas_w) / float(canvas_h)
    s_fit = min(float(canvas_w) / mat_w, float(canvas_h) / mat_h)
    return max(float(canvas_w) / (mat_w * s_fit), float(canvas_h) / (mat_h * s_fit))


# ----------------------------------------------------------------------------
# Tinh nang 1: keyframe cho track chinh
# ----------------------------------------------------------------------------

def apply_keyframes(draft, track_index, style, zoom=1.15, pan=0.15,
                    ease=True, points=9, only_photo=True, log=print):
    track = draft.data["tracks"][track_index]
    vids = {v.get("id"): v for v in draft.data["materials"].get("videos", [])}
    n = 0
    for i, seg in enumerate(track.get("segments", [])):
        if seg.get("desc") == "AKP_BG":
            continue
        mat = vids.get(seg.get("material_id")) or {}
        if only_photo and mat.get("type") != "photo":
            continue
        dur = int(seg.get("target_timerange", {}).get("duration") or 3000000)
        groups, meta = keyframes_for_style(dur, style, index=i, zoom=zoom,
                                           pan=pan, ease=ease, points=points)
        if not groups:
            continue
        seg["common_keyframes"] = groups
        apply_clip_meta(seg, meta)
        n += 1
    log("   + Keyframe: %d clip (kiểu: %s, ease=%s, %d điểm/đường)"
        % (n, style, "có" if ease else "không", points if ease else 2))
    return n


# ----------------------------------------------------------------------------
# Tinh nang 2: lop nen mo
# ----------------------------------------------------------------------------

def build_background_track(draft, main_track_index, alpha=0.30,
                           blur_name="Vừa", bg_motion=True, bg_zoom=1.08,
                           parallax=False, style=None, zoom=1.15, pan=0.15,
                           ease=True, points=9, include_video=True, log=print):
    """
    Tao 1 track NEN nam DUOI track anh chinh:
      - moi clip anh chinh -> 1 ban sao phong to phu kin khung (cover-scale)
      - alpha thap (mac dinh 0.30) => lui ve sau
      - bat canvas blur native cua CapCut => blur that, khong phai chi giam alpha
      - keyframe zoom cham 100% -> 108% => nen khong bi tinh cung
    """
    cw, ch = draft.canvas
    main = draft.data["tracks"][main_track_index]
    vids = {v.get("id"): v for v in draft.data["materials"].get("videos", [])}
    blur_val = CANVAS_BLUR_LEVELS.get(blur_name, 0.375)

    new_segments = []
    for i, seg in enumerate(main.get("segments", [])):
        if seg.get("desc") == "AKP_BG":
            continue                      # da la lop nen roi, khong nhan ban tiep
        mat = vids.get(seg.get("material_id")) or {}
        kind = mat.get("type")
        # clip video doc (vd 540x960) cung can lop nen, thuong con can hon anh vuong
        if kind != "photo" and not (include_video and kind == "video"):
            continue
        bg = clone_segment(draft, seg)
        mw, mh = material_size(mat)
        base = cover_scale(cw, ch, mw, mh)

        clip = bg.setdefault("clip", {})
        clip["alpha"] = float(alpha)
        clip.setdefault("scale", {})["x"] = base
        clip["scale"]["y"] = base
        clip.setdefault("transform", {})["x"] = 0.0
        clip["transform"]["y"] = 0.0
        bg["volume"] = 0.0
        bg["desc"] = "AKP_BG"
        try:
            bg["render_index"] = max(0, int(seg.get("render_index") or 1) - 1)
        except (TypeError, ValueError):
            pass

        set_canvas_blur(draft, bg, blur_val)

        dur = int(bg.get("target_timerange", {}).get("duration") or 3000000)
        if parallax and style:
            groups, meta = keyframes_for_style(dur, style, index=i, zoom=zoom,
                                               pan=pan, ease=ease, points=points,
                                               invert=True)
            # giu nguyen do phong to phu kin khung
            if groups:
                for g in groups:
                    if g["property_type"] in ("KFTypeScaleX", "KFTypeScaleY"):
                        for e in g["keyframe_list"]:
                            e["values"] = [round(e["values"][0] * base, 6)]
                if meta:
                    meta["scale_x"] = meta["scale_y"] = meta["uniform_scale_value"] = \
                        meta["scale_x"] * base
                bg["common_keyframes"] = groups
                apply_clip_meta(bg, meta)
        elif bg_motion:
            groups = [
                build_track("KFTypeScaleX", dur, base, base * bg_zoom, ease, points),
                build_track("KFTypeScaleY", dur, base, base * bg_zoom, ease, points),
                build_track("KFTypePositionX", dur, 0.0, 0.0, ease, 2),
                build_track("KFTypePositionY", dur, 0.0, 0.0, ease, 2),
            ]
            bg["common_keyframes"] = groups
            apply_clip_meta(bg, {"scale_x": base, "scale_y": base,
                                 "transform_x": 0.0, "transform_y": 0.0,
                                 "uniform_scale_on": False,
                                 "uniform_scale_value": base})
        new_segments.append(bg)

    if not new_segments:
        log("   ! Không tạo được lớp nền (track chính không có ảnh)")
        return None

    track = new_video_track(draft, new_segments)
    # chen NGAY DUOI track anh chinh
    draft.data["tracks"].insert(main_track_index, track)
    draft.state.setdefault("tracks", []).append(track["id"])
    log("   + Lớp nền: %d clip (alpha %.2f, blur '%s', %s)"
        % (len(new_segments), alpha, blur_name,
           "parallax ngược chiều" if parallax else
           ("zoom chậm %d%%" % round((bg_zoom - 1) * 100) if bg_motion else "tĩnh")))
    return track


# ----------------------------------------------------------------------------
# Tinh nang 3 + 4: letterbox 19:6 va vignette
# ----------------------------------------------------------------------------

def _overlay_photo_material(draft, png_path, w, h, duration_us):
    """
    Tao material anh cho lop phu. Clone tu 1 material photo co san
    de khop schema CapCut; neu draft chua co thi dung template toi thieu.
    """
    src = None
    for v in draft.data["materials"].get("videos", []):
        if v.get("type") == "photo":
            src = v
            break
    if src is not None:
        new = copy.deepcopy(src)
        new.update({
            "id": nid(),
            "path": png_path.replace("\\", "/"),
            "material_name": os.path.basename(png_path),
            "width": w, "height": h,
            "duration": max(int(duration_us), 5000000),
        })
        for k in ("local_material_id", "remote_material_id", "extra_type_option",
                  "crop_ratio", "aigc_type"):
            if k in new and isinstance(new[k], str):
                new[k] = new[k]
        draft.data["materials"]["videos"].append(new)
        draft.state.setdefault("materials", []).append(new["id"])
        return new

    new = {
        "id": nid(),
        "type": "photo",
        "path": png_path.replace("\\", "/"),
        "material_name": os.path.basename(png_path),
        "width": w, "height": h,
        "duration": max(int(duration_us), 5000000),
    }
    draft.data["materials"].setdefault("videos", []).append(new)
    draft.state.setdefault("materials", []).append(new["id"])
    return new


def register_in_meta(folder, png_path, w, h, duration_us, log=print):
    """
    Khai bao anh overlay trong draft_meta_info.json (muc 'draft_materials' type 0)
    de CapCut coi day la media hop le, khong bao mat file.
    Clone y nguyen schema cua 1 entry co san trong chinh file do.
    """
    meta_path = os.path.join(folder, "draft_meta_info.json")
    if not os.path.exists(meta_path):
        return False
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return False

    blocks = meta.get("draft_materials")
    if not isinstance(blocks, list) or not blocks:
        return False
    blk = None
    for b in blocks:
        if b.get("type") == 0 and isinstance(b.get("value"), list):
            blk = b
            break
    if blk is None:
        return False

    norm = png_path.replace("\\", "/")
    for v in blk["value"]:
        if (v.get("file_Path") or "").replace("\\", "/") == norm:
            return True  # da khai bao roi

    now = int(datetime.datetime.now().timestamp())
    if blk["value"]:
        entry = copy.deepcopy(blk["value"][0])
    else:
        entry = {"type": 0, "metetype": "photo", "item_source": 1,
                 "roughcut_time_range": {"duration": -1, "start": -1},
                 "sub_time_range": {"duration": -1, "start": -1}}
    entry.update({
        "id": str(uuid.uuid4()),
        "file_Path": norm,
        "extra_info": os.path.basename(png_path),
        "metetype": "photo",
        "type": 0,
        "width": int(w),
        "height": int(h),
        "duration": int(duration_us),
        "create_time": now,
        "import_time": now,
        "import_time_ms": now * 1000000,
        "md5": "",
    })
    blk["value"].append(entry)
    meta["tm_draft_modified"] = int(datetime.datetime.now().timestamp() * 1000000)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4, ensure_ascii=False)
        return True
    except Exception as ex:
        log("   ! Không ghi được draft_meta_info.json: %s" % ex)
        return False


def add_overlay_track(draft, png_path, alpha=1.0, name="AKP_OVERLAY", log=print):
    """Chen 1 anh PNG phu kin timeline, nam TREN CUNG."""
    if not os.path.exists(png_path):
        log("   ! Không tìm thấy asset: %s" % png_path)
        return None
    cw, ch = draft.canvas
    dur = draft.duration or 10000000

    # tim 1 segment photo mau de clone (dam bao du truong)
    tpl = None
    vids = {v.get("id"): v for v in draft.data["materials"].get("videos", [])}
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            if (vids.get(s.get("material_id")) or {}).get("type") == "photo":
                tpl = s
                break
        if tpl:
            break

    mat = _overlay_photo_material(draft, png_path, cw, ch, dur)
    register_in_meta(draft.folder, png_path, cw, ch, dur, log=log)

    if tpl is not None:
        seg = clone_segment(draft, tpl)
    else:
        seg = {
            "id": nid(),
            "material_id": "",
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                     "transform": {"x": 0.0, "y": 0.0},
                     "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": False, "value": 1.0},
            "extra_material_refs": [],
            "common_keyframes": [],
            "keyframe_refs": [],
            "visible": True,
            "volume": 0.0,
            "speed": 1.0,
        }
        draft.state.setdefault("segments", []).append(seg["id"])

    # lop phu phai nam tren cung: render_index lon hon moi clip video hien co
    max_ri = 0
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            try:
                max_ri = max(max_ri, int(s.get("render_index") or 0))
            except (TypeError, ValueError):
                pass
    seg["render_index"] = max_ri + 1

    seg["material_id"] = mat["id"]
    seg["target_timerange"] = {"start": 0, "duration": int(dur)}
    seg["source_timerange"] = {"start": 0, "duration": int(dur)}
    seg["volume"] = 0.0
    seg["desc"] = name
    seg["common_keyframes"] = []
    clip = seg.setdefault("clip", {})
    clip["alpha"] = float(alpha)
    clip.setdefault("scale", {})["x"] = 1.0
    clip["scale"]["y"] = 1.0
    clip.setdefault("transform", {})["x"] = 0.0
    clip["transform"]["y"] = 0.0

    track = new_video_track(draft, [seg])
    draft.data["tracks"].append(track)   # cuoi mang = tren cung
    draft.state.setdefault("tracks", []).append(track["id"])
    log("   + Lớp phủ '%s' (alpha %.2f) phủ %.1fs" % (name, alpha, dur / 1000000.0))
    return track


# ----------------------------------------------------------------------------
# Cac buoc "don dep" cua che do Chuan Pro
# ----------------------------------------------------------------------------

CANVAS_PRESETS = {
    "Giữ nguyên": None,
    "16:9 YouTube (1920×1080)": (1920, 1080, "16:9"),
    "9:16 Shorts (1080×1920)": (1080, 1920, "9:16"),
    "1:1 vuông (1920×1920)": (1920, 1920, "1:1"),
    "19:6 siêu rộng (2280×720)": (2280, 720, "original"),
}


def set_canvas_ratio(draft, preset_name, log=print):
    """
    Doi ti le khung hinh cua du an.

    QUAN TRONG: lop nen mo chi co tac dung khi anh KHONG lap day khung.
    Anh vuong trong canvas vuong thi phu kin 100%, he so cover = 1.0,
    lop nen nam dung sau lop chinh nen khong nhin thay gi.
    Doi sang 16:9 la anh vuong chi con chiem phan giua, hai ben lo ra
    va lop nen mo moi phat huy tac dung.
    """
    preset = CANVAS_PRESETS.get(preset_name)
    if not preset:
        return False
    w, h, ratio = preset
    cfg = draft.data.setdefault("canvas_config", {})
    old = (cfg.get("width"), cfg.get("height"))
    if old == (w, h):
        return False
    cfg["width"], cfg["height"], cfg["ratio"] = w, h, ratio
    log("   + Khung hình: %sx%s -> %dx%d (%s)" % (old[0], old[1], w, h, ratio))
    return True


def count_flat_clips(draft):
    """
    Dem so clip LAP DAY khung (he so phu ~ 1.0).
    Nhung clip nay se khong nhin thay lop nen mo, vi lop nen nam dung sau
    lop chinh va co cung kich thuoc. Day la nguyen nhan pho bien nhat
    khien nguoi dung tuong tool "khong chay".
    Tra ve (so_clip_lap_day, tong_so_clip).
    """
    cw, ch = draft.canvas
    vids = {v["id"]: v for v in draft.data["materials"].get("videos", [])}
    flat = total = 0
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            if s.get("desc") in AKP_MARKS:
                continue
            mat = vids.get(s.get("material_id")) or {}
            if mat.get("type") not in ("photo", "video"):
                continue
            total += 1
            if cover_scale(cw, ch, *material_size(mat)) < 1.02:
                flat += 1
    return flat, total


def fix_track_overlaps(draft, log=print):
    """
    Day cac track phia tren cho het chong lan voi track duoi.
    Loi thuong gap khi cat tay: track sau bat dau truoc khi track truoc ket thuc
    -> nhay vai frame sai hinh ngay diem noi.
    """
    vtracks = [t for t in draft.data.get("tracks", []) if t.get("type") == "video"
               and t.get("segments")]
    if len(vtracks) < 2:
        return 0
    fixed = 0
    prev_end = None
    for t in vtracks:
        segs = t["segments"]
        start = min(s["target_timerange"]["start"] for s in segs)
        end = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                  for s in segs)
        if prev_end is not None and 0 < prev_end - start <= 2000000:
            shift = prev_end - start
            for s in segs:
                s["target_timerange"]["start"] += shift
            end += shift
            fixed += 1
            log("   + Đẩy 1 track lùi %.2fs cho hết chồng lấn" % (shift / 1000000.0))
        prev_end = max(prev_end or 0, end)

    total = 0
    for t in draft.data.get("tracks", []):
        for s in t.get("segments", []):
            tr = s.get("target_timerange") or {}
            total = max(total, int(tr.get("start", 0)) + int(tr.get("duration", 0)))
    if total:
        draft.data["duration"] = total
    return fixed


def fit_video_clips(draft, gentle_zoom=1.05, ease=True, points=11, log=print):
    """
    Clip video (nhat la video doc 9:16) de vua khung thay vi phong qua kho.
    Phong qua kho = cat mat tren duoi cua khung doc; gio de scale 1.0 + zoom rat nhe.
    """
    vids = {v.get("id"): v for v in draft.data["materials"].get("videos", [])}
    n = 0
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            if s.get("desc") == "AKP_BG":
                continue
            mat = vids.get(s.get("material_id")) or {}
            if mat.get("type") != "video":
                continue
            dur = int(s.get("target_timerange", {}).get("duration") or 3000000)
            s["common_keyframes"] = [
                build_track("KFTypeScaleX", dur, 1.0, gentle_zoom, ease, points),
                build_track("KFTypeScaleY", dur, 1.0, gentle_zoom, ease, points),
                build_track("KFTypePositionX", dur, 0.0, 0.0, ease, 2),
                build_track("KFTypePositionY", dur, 0.0, 0.0, ease, 2),
            ]
            apply_clip_meta(s, {"scale_x": 1.0, "scale_y": 1.0,
                                "transform_x": 0.0, "transform_y": 0.0,
                                "uniform_scale_on": False, "uniform_scale_value": 1.0})
            n += 1
            log("   + Clip video %sx%s: để vừa khung, zoom nhẹ %d%%"
                % (mat.get("width"), mat.get("height"),
                   round((gentle_zoom - 1) * 100)))
    return n


def blur_canvas_on_foreground(draft, blur_name="Vừa", log=print):
    """
    Bat 'Phông nền mờ' native cua CapCut cho cac clip CHINH (khong phai lop nen).
    Clip chinh khong phu kin khung nen phan lo ra hai ben se duoc CapCut to
    bang ban blur cua chinh no - blur that, khong can effect_id.
    """
    val = CANVAS_BLUR_LEVELS.get(blur_name, 0.375)
    n = 0
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            if s.get("desc") == "AKP_BG":
                continue
            if set_canvas_blur(draft, s, val):
                n += 1
    log("   + Canvas Blur '%s' cho %d clip chính" % (blur_name, n))
    return n


# ----------------------------------------------------------------------------
# Go sach nhung gi AKP da tao
# ----------------------------------------------------------------------------

AKP_MARKS = ("AKP_BG", "AKP_VIGNETTE", "AKP_LETTERBOX_19_6", "AKP_SUB")


def cleanup_orphans(draft, log=print):
    """
    Go cac track do AKP tao ra nhung khong con trong akp_state.json
    (vi du file trang thai bi xoa). Nhan dien bang nhan 'desc' nen
    lop nen nguoi dung tu lam bang tay khong bao gio bi dung toi.
    """
    keep = []
    removed = 0
    for t in draft.data.get("tracks", []):
        segs = t.get("segments") or []
        if (t.get("type") in ("video", "text") and segs
                and all(s.get("desc") in AKP_MARKS for s in segs)):
            removed += 1
            continue
        keep.append(t)
    if removed:
        draft.data["tracks"] = keep
        log("   + Đã gỡ %d track cũ của AKP còn sót lại" % removed)
    return removed


def cleanup_previous(draft, log=print):
    # bat lai phu de goc da an o lan chay truoc, de lan nay quyet dinh lai tu dau
    try:
        import sys as _sys
        import akp_subtitle
        akp_subtitle.unhide_original_subtitles(_sys.modules[__name__], draft)
    except Exception:
        pass
    st = draft.state
    tids = set(st.get("tracks", []))
    sids = set(st.get("segments", []))
    mids = set(st.get("materials", []))
    if not (tids or sids or mids):
        cleanup_orphans(draft, log)
        return 0

    before = len(draft.data.get("tracks", []))
    draft.data["tracks"] = [t for t in draft.data.get("tracks", [])
                            if t.get("id") not in tids]
    for t in draft.data.get("tracks", []):
        t["segments"] = [s for s in t.get("segments", []) if s.get("id") not in sids]
    for cat, lst in draft.data.get("materials", {}).items():
        if isinstance(lst, list):
            draft.data["materials"][cat] = [o for o in lst
                                            if not (isinstance(o, dict) and o.get("id") in mids)]
    removed = before - len(draft.data["tracks"])
    st["tracks"], st["segments"], st["materials"] = [], [], []
    if removed or sids:
        log("   + Đã gỡ %d track / %d clip do lần chạy trước tạo ra" % (removed, len(sids)))
    return removed


# ----------------------------------------------------------------------------
# Ham tong
# ----------------------------------------------------------------------------

def process(folder, opts, log=print):
    """
    opts: dict
      track_index, style, zoom, pan, ease, points,
      bg_on, bg_alpha, bg_blur, bg_motion, bg_zoom, parallax,
      letterbox_on, letterbox_png, vignette_on, vignette_png, vignette_alpha
    """
    draft = Draft(folder)
    cw, ch = draft.canvas
    log("== Dự án: %s" % os.path.basename(folder))
    log("   Canvas %d×%d | thời lượng %.1fs | %d track"
        % (cw, ch, draft.duration / 1000000.0, len(draft.data.get("tracks", []))))

    bk = draft.backup()
    log("   + Backup: %s" % os.path.basename(bk))

    cleanup_previous(draft, log)
    set_canvas_ratio(draft, opts.get("canvas") or "Giữ nguyên", log)

    ti = opts.get("track_index")
    if ti is None:
        ti = draft.guess_main_track()
    if ti is None:
        raise ValueError("Không tìm thấy track ảnh nào trong dự án")

    style = opts.get("style") or "Không Sử Dụng"
    parallax = (style == "Parallax 19:6 (nền ngược chiều)")

    if style != "Không Sử Dụng":
        apply_keyframes(draft, ti, style,
                        zoom=opts.get("zoom", 1.15),
                        pan=opts.get("pan", 0.15),
                        ease=opts.get("ease", True),
                        points=opts.get("points", 9),
                        log=log)

    if opts.get("bg_on", True):
        build_background_track(draft, ti,
                               alpha=opts.get("bg_alpha", 0.30),
                               blur_name=opts.get("bg_blur", "Vừa"),
                               bg_motion=opts.get("bg_motion", True),
                               bg_zoom=opts.get("bg_zoom", 1.08),
                               parallax=parallax,
                               style=style,
                               zoom=opts.get("zoom", 1.15),
                               pan=opts.get("pan", 0.15),
                               ease=opts.get("ease", True),
                               points=opts.get("points", 9),
                               log=log)

    if opts.get("vignette_on", True) and opts.get("vignette_png"):
        add_overlay_track(draft, opts["vignette_png"],
                          alpha=opts.get("vignette_alpha", 0.45),
                          name="AKP_VIGNETTE", log=log)

    if opts.get("letterbox_on", True) and opts.get("letterbox_png"):
        add_overlay_track(draft, opts["letterbox_png"], alpha=1.0,
                          name="AKP_LETTERBOX_19_6", log=log)

    renumber_tracks(draft)
    draft.save()
    log("   => ĐÃ LƯU draft_content.json")
    verify_saved(folder, log)
    log("   Mở lại CapCut và bấm vào dự án để thấy kết quả.")
    return draft


# ----------------------------------------------------------------------------
# CHE DO CHUAN PRO - mot nut, lam het moi track
# ----------------------------------------------------------------------------

PRO_DEFAULTS = {
    "style": "Ken Burns Pro (Zoom + Pan chéo)",
    "zoom": 1.12,
    "pan": 0.10,
    "points": 11,
    "ease": True,
    "bg_alpha": 0.30,
    "bg_blur": "Vừa",
    "bg_zoom": 1.08,
    "canvas_blur": True,
    "fit_video": True,
    "fix_overlap": True,
    "video_zoom": 1.05,
    "canvas": "Giữ nguyên",
}


def process_pro(folder, opts=None, log=print):
    """
    Cong thuc 'Chuan Pro': ap dung cho TAT CA track video trong du an.

      1. Sua chong lan giua cac track (loi nhay vai frame o diem noi)
      2. Keyframe Ken Burns Pro co ease, zoom-in / zoom-out luan phien giua
         cac anh lien ke cho do don dieu
      3. Clip video (nhat la video doc) de vua khung + zoom rat nhe
      4. Moi track deu duoc mot lop nen phu kin khung, alpha thap, zoom cham
      5. Bat Canvas Blur native cua CapCut cho toan bo clip chinh

    Day dung la quy trinh da dung cho du an 0915 (1).
    """
    o = dict(PRO_DEFAULTS)
    if opts:
        o.update({k: v for k, v in opts.items() if v is not None})

    draft = Draft(folder)
    cw, ch = draft.canvas
    log("== Dự án: %s" % os.path.basename(folder))
    log("   Canvas %d×%d | thời lượng %.1fs | %d track"
        % (cw, ch, draft.duration / 1000000.0, len(draft.data.get("tracks", []))))

    bk = draft.backup()
    log("   + Backup: %s" % os.path.basename(bk))
    cleanup_previous(draft, log)

    set_canvas_ratio(draft, o.get("canvas") or "Giữ nguyên", log)
    cw, ch = draft.canvas

    if o.get("fix_overlap", True):
        fix_track_overlaps(draft, log)

    # canh bao khi lop nen se khong nhin thay duoc
    flat, total = count_flat_clips(draft)
    if flat:
        log("   ! %d/%d clip lấp đầy khung %d×%d nên lớp nền mờ của chúng SẼ KHÔNG THẤY"
            % (flat, total, cw, ch))
        log("     (ảnh vuông trong khung vuông). Đổi 'Tỉ lệ khung hình' sang "
            "16:9 YouTube rồi chạy lại.")

    # cac track video co clip, tinh theo chi so hien tai.
    # Bo qua track chi gom lop nen AKP (truong hop akp_state.json bi mat)
    # de khong chong nen len nen.
    targets = []
    skipped = 0
    for i, t in enumerate(draft.data.get("tracks", [])):
        segs = t.get("segments") or []
        if t.get("type") != "video" or not segs:
            continue
        if all(s.get("desc") == "AKP_BG" for s in segs):
            skipped += 1
            continue
        targets.append(i)
    if skipped:
        log("   + Bỏ qua %d track lớp nền có sẵn (không chồng nền lên nền)" % skipped)
    if not targets:
        raise ValueError("Dự án không có track video nào có clip")

    total_kf = 0
    for ti in targets:
        total_kf += apply_keyframes(draft, ti, o["style"], zoom=o["zoom"],
                                    pan=o["pan"], ease=o["ease"],
                                    points=o["points"], only_photo=True, log=log)

    if o.get("fit_video", True):
        fit_video_clips(draft, gentle_zoom=o.get("video_zoom", 1.05),
                        ease=o["ease"], points=o["points"], log=log)

    # dung tu chi so cao xuong thap: chen track moi khong lam lech chi so con lai
    for ti in sorted(targets, reverse=True):
        build_background_track(draft, ti, alpha=o["bg_alpha"],
                               blur_name=o["bg_blur"], bg_motion=True,
                               bg_zoom=o["bg_zoom"], include_video=True,
                               ease=o["ease"], points=o["points"], log=log)

    if o.get("canvas_blur", True):
        blur_canvas_on_foreground(draft, o["bg_blur"], log=log)

    source = o.get("srt_source")      # "auto" | duong dan .srt | None
    if source:
        try:
            import sys as _sys
            import akp_subtitle
            me = _sys.modules[__name__]
            akp_subtitle.unhide_original_subtitles(me, draft)
            n = akp_subtitle.build_keyword_subtitles(
                me, draft, source,
                o.get("app_dir") or os.path.dirname(os.path.abspath(__file__)),
                style_name=o.get("sub_style") or "Chữ to giữa dưới, viền đen",
                max_words=o.get("sub_max_words", 3),
                upper=o.get("sub_upper", False),
                strict=o.get("sub_strict", False), log=log)
            if n and o.get("sub_hide_original", True):
                akp_subtitle.hide_original_subtitles(me, draft, log)
        except Exception as ex:
            log("   ! Lỗi tạo phụ đề từ khoá: %s" % ex)

    renumber_tracks(draft)
    draft.save()
    log("   => ĐÃ LƯU draft_content.json (%d clip có keyframe mới)" % total_kf)
    verify_saved(folder, log)
    log("   Mở lại CapCut và bấm vào dự án để thấy kết quả.")
    return draft


def find_latest_draft(draft_dir):
    """Tra ve (ten, duong dan) cua du an CapCut sua gan day nhat."""
    best, best_mt = None, -1
    if not os.path.isdir(draft_dir):
        return None
    for name in os.listdir(draft_dir):
        p = os.path.join(draft_dir, name)
        cf = os.path.join(p, CONTENT_FILE)
        if os.path.isdir(p) and os.path.exists(cf):
            mt = os.path.getmtime(cf)
            if mt > best_mt:
                best, best_mt = (name, p), mt
    return best
