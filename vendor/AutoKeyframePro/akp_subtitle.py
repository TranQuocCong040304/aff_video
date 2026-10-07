# -*- coding: utf-8 -*-
"""
AKP SUBTITLE - Phu de TU KHOA cho CapCut

Khac voi phu de thong thuong (chep nguyen van moi cau), module nay chi rut ra
TU KHOA chinh cua moi cau trong file .srt roi hien len man hinh:
  - so lieu va don vi   : 8kg, 1500W, 2 nam, 70%, 15 trieu
  - ma san pham / ten   : AQH-V800H, AQUA, Inverter
  - danh tu quan trong  : may say, long say, bao hanh, tiet kiem dien

Chu duoc dat theo tung CLIP ANH: mot clip hien mot cum tu khoa, suot thoi
gian clip do, nen khong bi nhap nhay theo tung cau noi.
"""

import os
import re
import json
import copy
import unicodedata

# ----------------------------------------------------------------------------
# Doc file .srt
# ----------------------------------------------------------------------------

TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _to_us(h, m, s, ms):
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)) * 1000


def parse_srt(path):
    """Tra ve danh sach {'start','end','text'} tinh bang micro giay."""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if raw is None:
        raise ValueError("Không đọc được file SRT (bảng mã lạ)")

    items = []
    block_lines = []

    def flush(lines):
        if not lines:
            return
        start = end = None
        text_lines = []
        for ln in lines:
            mt = TIME_RE.search(ln)
            if mt and start is None:
                g = mt.groups()
                start = _to_us(g[0], g[1], g[2], g[3].ljust(3, "0"))
                end = _to_us(g[4], g[5], g[6], g[7].ljust(3, "0"))
            elif ln.strip().isdigit() and not text_lines and start is None:
                continue
            elif not mt:
                text_lines.append(ln.strip())
        text = " ".join(x for x in text_lines if x).strip()
        if start is not None and text:
            items.append({"start": start, "end": end, "text": text})

    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip() == "":
            flush(block_lines)
            block_lines = []
        else:
            block_lines.append(line)
    flush(block_lines)
    return items


def srt_items_from_draft(engine, draft):
    """
    Lay phu de CapCut DA TU TAO san trong du an (tinh nang 'Tao phu de tu dong').
    Khoi phai xuat file .srt roi chon lai bang tay.
    """
    texts = {m.get("id"): m for m in draft.data["materials"].get("texts", [])}
    items = []
    for t in draft.data.get("tracks", []):
        if t.get("type") != "text":
            continue
        for s in t.get("segments", []) or []:
            if s.get("desc") in engine.AKP_MARKS:
                continue                       # bo qua chu do chinh AKP tao
            m = texts.get(s.get("material_id")) or {}
            txt = ""
            try:
                txt = (json.loads(m.get("content") or "{}") or {}).get("text", "")
            except Exception:
                txt = m.get("base_content") or m.get("recognize_text") or ""
            txt = re.sub(r"\s+", " ", (txt or "").replace("\n", " ")).strip()
            if not txt:
                continue
            tr = s.get("target_timerange") or {}
            st = int(tr.get("start", 0))
            items.append({"start": st,
                          "end": st + int(tr.get("duration", 0)),
                          "text": txt})
    items.sort(key=lambda x: x["start"])
    return items


def _srt_time(us):
    ms = int(us) // 1000
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, (ms % 3600000) // 60000,
                                    (ms % 60000) // 1000, ms % 1000)


def export_srt(items, path):
    """Ghi danh sach cau ra file .srt chuan."""
    with open(path, "w", encoding="utf-8") as f:
        for i, it in enumerate(items, 1):
            f.write("%d\n%s --> %s\n%s\n\n"
                    % (i, _srt_time(it["start"]), _srt_time(it["end"]), it["text"]))
    return path


def hide_original_subtitles(engine, draft, log=print):
    """
    An phu de goc (189 cau day man hinh) de chi con lai chu tu khoa.
    Khong xoa - chi tat hien thi, va ghi lai id de lan sau bat lai duoc.
    """
    ids = []
    for t in draft.data.get("tracks", []):
        if t.get("type") != "text":
            continue
        for s in t.get("segments", []) or []:
            if s.get("desc") in engine.AKP_MARKS:
                continue
            s["visible"] = False
            s.setdefault("clip", {})["alpha"] = 0.0
            ids.append(s.get("id"))
    if ids:
        draft.state["hidden_subs"] = sorted(set(
            (draft.state.get("hidden_subs") or []) + ids))
        log("   + Đã ẩn %d câu phụ đề gốc (chỉ còn chữ từ khoá)" % len(ids))
    return len(ids)


def unhide_original_subtitles(engine, draft):
    """Bat lai nhung cau phu de da bi an o lan chay truoc."""
    ids = set(draft.state.get("hidden_subs") or [])
    if not ids:
        return 0
    n = 0
    for t in draft.data.get("tracks", []):
        for s in t.get("segments", []) or []:
            if s.get("id") in ids:
                s["visible"] = True
                s.setdefault("clip", {})["alpha"] = 1.0
                n += 1
    draft.state["hidden_subs"] = []
    return n


# ----------------------------------------------------------------------------
# Rut tu khoa
# ----------------------------------------------------------------------------

# Tu khong mang thong tin - bo qua khi cham diem
STOPWORDS = set("""
a ai anh ay bao bang bi boi ca cac cai cang cho chi chiec chinh chu chung co con
cua cung cuoi da dang de den deu di do doi dong du dua duoc gi gia hay he hoac
hon i khi khong la lai lam len loai luc ma mot muon nao nay nen nhu nhung no
o phai qua ra rang rat roi sau se su ta thi the theo tren tu tuy va vao vay ve
vi voi vua xin y minh ban toi chung_toi nay_la that ra_sao lam_sao the_nao
""".split())

UNIT_RE = re.compile(
    r"^\d+([.,]\d+)?\s*(kg|g|w|kw|kwh|l|lit|lít|inch|cm|mm|m|năm|nam|thang|tháng|"
    r"%|trieu|triệu|nghin|nghìn|đ|d|vnd|hz|v|a)?$", re.I)

MODEL_RE = re.compile(r"^[A-Z]{2,}[-_.]?[A-Z0-9][A-Z0-9.\-_]*\d", re.I)


def _strip_accent(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def _score(word, idx, total):
    """Cham diem mot tu: cao = dang lam tu khoa."""
    w = word.strip(".,;:!?()[]\"'“”…-")
    if not w:
        return -1
    bare = _strip_accent(w)

    if bare in STOPWORDS:
        return -1
    if len(bare) <= 1:
        return -1

    score = 0.0
    if re.search(r"\d", w):
        score += 6.0                       # con so luon la thong tin manh
    if UNIT_RE.match(w):
        score += 3.0                       # co don vi di kem
    if MODEL_RE.match(w):
        score += 5.0                       # ma san pham
    if w.isupper() and len(w) >= 2:
        score += 3.0                       # AQUA, LG, OLED
    elif w[:1].isupper() and idx > 0:
        score += 1.5                       # ten rieng giua cau
    score += min(len(bare), 10) * 0.15     # tu dai thuong nhieu nghia hon
    if idx < total * 0.4:
        score += 0.5                       # dau cau thuong la chu de
    return score


def _clean(w):
    return w.strip(".,;:!?()[]\"'“”…")


def extract_keyword(text, max_words=3):
    """
    Rut CUM TU KHOA lien mach tu mot cau.

    Khong nhat cac tu roi rac khap cau (se ra kieu 'Cong thong 30%' doc khong
    xuoi) ma truot mot cua so lien tiep toi da max_words tu, cham diem ca cum,
    roi chon cum cao diem nhat. Nho vay cum chu luon la mot manh cua cau goc:
    'bao hanh 2 nam', 'say 8kg', '7 trieu 490'.
    """
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return ""
    total = len(words)
    base = [_score(w, i, total) for i, w in enumerate(words)]
    if all(s <= 0 for s in base):
        return ""

    max_words = max(1, int(max_words))
    best, best_val = None, -1e9

    for size in range(1, max_words + 1):
        for i in range(0, total - size + 1):
            j = i + size
            # hai dau cum phai la tu co nghia, khong bat dau/ket thuc bang tu dem
            if base[i] <= 0 or base[j - 1] <= 0:
                continue
            chunk = base[i:j]
            seg = words[i:j]
            val = sum(s if s > 0 else 0.0 for s in chunk)
            # tu dem nam GIUA cum thi chap nhan duoc, chi tru nhe
            val -= sum(0.8 for s in chunk if s <= 0)
            # thuong cho cum nhieu tu co nghia -> chu doc thanh cum, khong cut lui
            val += 0.9 * (sum(1 for s in chunk if s > 0) - 1)
            if any(re.search(r"\d", w) for w in seg):
                val += 2.0
            if any(_clean(w).isupper() and len(_clean(w)) >= 2 for w in seg):
                val += 1.5
            val /= (size ** 0.15)          # can bang: khong qua ngan, khong lan man
            if val > best_val:
                best_val, best = val, (i, j)

    if not best:
        return ""
    i, j = best
    return " ".join(_clean(w) for w in words[i:j] if _clean(w)).strip()


def keywords_for_clips(srt_items, clips, max_words=3, upper=False):
    """
    Voi moi clip (start, duration) tim cac cau .srt giao thoi gian voi no,
    rut tu khoa cua cau chiem nhieu thoi luong nhat.
    Tra ve list cung do dai voi clips, phan tu la chuoi (co the rong).
    """
    out = []
    for c in clips:
        cs = c["start"]
        ce = c["start"] + c["duration"]
        best, best_overlap = None, 0
        for it in srt_items:
            ov = min(ce, it["end"]) - max(cs, it["start"])
            if ov > best_overlap:
                best, best_overlap = it, ov
        kw = extract_keyword(best["text"], max_words) if best else ""
        if upper and kw:
            kw = kw.upper()
        # hai clip lien tiep bam cung mot cau -> khong lap lai y het chu
        if out and kw and kw == out[-1]:
            kw = ""
        out.append(kw)
    return out


# ----------------------------------------------------------------------------
# Dung track text trong draft
# ----------------------------------------------------------------------------

STYLE_PRESETS = {
    "Chữ to giữa dưới, viền đen": {
        "font_size": 12.0,
        "text_color": "#FFFFFF",
        "border_color": "#000000",
        "border_width": 0.12,
        "alignment": 1,
        "pos_y": -0.62,          # 1/5 duoi khung
        "pos_x": 0.0,
        "scale": 1.0,
        "has_shadow": True,
        "shadow_color": "#000000",
    },
    "Box màu nổi bật": {
        "font_size": 10.0,
        "text_color": "#FFFFFF",
        "border_color": "",
        "border_width": 0.0,
        "alignment": 0,
        "pos_y": -0.66,
        "pos_x": -0.42,
        "scale": 1.0,
        "background_color": "#E8541E",
        "background_alpha": 0.92,
        "has_shadow": False,
    },
    "Chữ lớn giữa khung": {
        "font_size": 18.0,
        "text_color": "#FFFFFF",
        "border_color": "#000000",
        "border_width": 0.14,
        "alignment": 1,
        "pos_y": 0.0,
        "pos_x": 0.0,
        "scale": 1.0,
        "has_shadow": True,
        "shadow_color": "#000000",
    },
}


def _load_template(app_dir):
    p = os.path.join(app_dir, "assets", "text_template.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _make_content(text, style):
    """Chuoi JSON trong truong 'content' - dinh dang chu cua CapCut."""
    return json.dumps({
        "text": text,
        "styles": [{
            "fill": {"content": {"solid": {"color": _hex_to_rgb(
                style.get("text_color", "#FFFFFF"))}},
                "alpha": 1.0},
            "font": {"id": "", "path": ""},
            "size": style.get("font_size", 12.0),
            "range": [0, len(text)],
            "strokes": ([{
                "content": {"solid": {"color": _hex_to_rgb(
                    style.get("border_color") or "#000000")}},
                "width": style.get("border_width", 0.12),
                "alpha": 1.0,
            }] if style.get("border_width") else []),
        }],
    }, ensure_ascii=False)


def _hex_to_rgb(h):
    h = (h or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def build_keyword_subtitles(engine, draft, source, app_dir,
                            style_name="Chữ to giữa dưới, viền đen",
                            max_words=3, upper=False, skip_empty=True,
                            strict=False, log=print):
    """
    Tao mot track text moi: moi clip anh mot cum tu khoa, hien suot clip do.

    'source' co the la:
      - duong dan file .srt
      - chuoi "auto"  -> lay phu de CapCut da tu tao san trong du an
      - danh sach item {'start','end','text'} co san
    Tra ve so chu da chen.
    """
    if isinstance(source, list):
        items = source
    elif source == "auto":
        items = srt_items_from_draft(engine, draft)
        if not items:
            log("   ! Dự án chưa có phụ đề nào để lấy từ khoá.")
            log("     Dùng 'Tạo phụ đề tự động' trong CapCut trước, rồi chạy lại.")
            return 0
        log("   + Lấy phụ đề sẵn có trong dự án: %d câu" % len(items))
    else:
        items = parse_srt(source)
        if not items:
            log("   ! File SRT không có dòng nào đọc được")
            return 0
        log("   + Đọc SRT: %d câu, dài %.1fs"
            % (len(items), items[-1]["end"] / 1000000.0))

    tpl = _load_template(app_dir)
    if not tpl:
        log("   ! Thiếu assets/text_template.json — không tạo được chữ")
        return 0

    style = dict(STYLE_PRESETS.get(style_name)
                 or STYLE_PRESETS["Chữ to giữa dưới, viền đen"])

    # lay danh sach clip anh cua track chinh (bo lop nen AKP)
    vids = {v["id"]: v for v in draft.data["materials"].get("videos", [])}
    clips = []
    for t in draft.video_tracks():
        for s in t.get("segments", []):
            if s.get("desc") in engine.AKP_MARKS:
                continue
            mat = vids.get(s.get("material_id")) or {}
            if mat.get("type") not in ("photo", "video"):
                continue
            tr = s["target_timerange"]
            clips.append({"start": tr["start"], "duration": tr["duration"]})
    if not clips:
        log("   ! Không tìm thấy clip ảnh nào để gắn chữ")
        return 0
    clips.sort(key=lambda c: c["start"])

    kws = keywords_for_clips(items, clips, max_words=max_words, upper=upper)

    if strict:
        # chi giu cum co so lieu hoac ten rieng viet hoa - chu se thua hon han
        kept = []
        for k in kws:
            ok = bool(k) and (re.search(r"\d", k) or
                              any(_clean(w).isupper() and len(_clean(w)) >= 2
                                  for w in k.split()))
            kept.append(k if ok else "")
        kws = kept

    segments = []
    n = 0
    for clip, kw in zip(clips, kws):
        if not kw and skip_empty:
            continue
        mat = copy.deepcopy(tpl["material"])
        mat["id"] = engine.nid()
        mat["content"] = _make_content(kw, style)
        mat["base_content"] = kw
        mat["recognize_text"] = kw
        mat["font_size"] = style.get("font_size", 12.0)
        mat["text_color"] = style.get("text_color", "#FFFFFF")
        mat["border_color"] = style.get("border_color", "")
        mat["border_width"] = style.get("border_width", 0.0)
        mat["border_alpha"] = 1.0 if style.get("border_width") else 0.0
        mat["alignment"] = style.get("alignment", 1)
        mat["has_shadow"] = bool(style.get("has_shadow"))
        if style.get("shadow_color"):
            mat["shadow_color"] = style["shadow_color"]
        if style.get("background_color"):
            mat["background_color"] = style["background_color"]
            mat["background_alpha"] = style.get("background_alpha", 0.9)
            mat["background_style"] = 1
        draft.data["materials"].setdefault("texts", []).append(mat)
        draft.state.setdefault("materials", []).append(mat["id"])

        seg = copy.deepcopy(tpl["segment"])
        seg["id"] = engine.nid()
        seg["material_id"] = mat["id"]
        seg["desc"] = "AKP_SUB"
        seg["target_timerange"] = {"start": int(clip["start"]),
                                   "duration": int(clip["duration"])}
        seg["source_timerange"] = None
        seg["common_keyframes"] = []
        seg["keyframe_refs"] = []
        clipd = seg.setdefault("clip", {})
        clipd.setdefault("scale", {})["x"] = style.get("scale", 1.0)
        clipd["scale"]["y"] = style.get("scale", 1.0)
        clipd.setdefault("transform", {})["x"] = style.get("pos_x", 0.0)
        clipd["transform"]["y"] = style.get("pos_y", -0.62)
        clipd["alpha"] = 1.0

        refs = []
        for ex in tpl.get("extras", []):
            obj = copy.deepcopy(ex["object"])
            obj["id"] = engine.nid()
            draft.data["materials"].setdefault(ex["category"], []).append(obj)
            draft.state.setdefault("materials", []).append(obj["id"])
            refs.append(obj["id"])
        seg["extra_material_refs"] = refs

        draft.state.setdefault("segments", []).append(seg["id"])
        segments.append(seg)
        n += 1

    if not segments:
        log("   ! Không rút được từ khoá nào từ SRT")
        return 0

    track = copy.deepcopy(tpl.get("track") or {})
    track.update({"id": engine.nid(), "type": "text", "segments": segments})
    draft.data["tracks"].append(track)            # tren cung
    draft.state.setdefault("tracks", []).append(track["id"])

    log("   + Phụ đề từ khoá: %d chữ (kiểu '%s', tối đa %d từ)"
        % (n, style_name, max_words))
    preview = [k for k in kws if k][:6]
    if preview:
        log("     Ví dụ: " + " | ".join(preview))
    return n
