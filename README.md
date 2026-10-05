# 🎬 Auto Video Affiliate

Tool tự động dựng video review **Top 5 sản phẩm affiliate Shopee** cho YouTube:

**chọn sản phẩm → viết script bám ảnh/video thật → audio có cảm xúc → ghép video → log lịch sử & hiệu suất**

---

## 📑 Mục lục

- [Tính năng chính](#-tính-năng-chính)
- [Kiến trúc](#-kiến-trúc)
- [Lưu ý trước khi dùng](#️-lưu-ý-trước-khi-dùng)
- [Cài đặt](#-cài-đặt)
- [Giao diện web (Streamlit)](#-giao-diện-web-streamlit)
- [Chuẩn bị dữ liệu đầu vào](#-chuẩn-bị-dữ-liệu-đầu-vào)
- [Chạy bằng CLI](#-chạy-bằng-cli)
- [Kết quả đầu ra](#-kết-quả-đầu-ra)
- [Log lịch sử & hiệu suất](#-log-lịch-sử--hiệu-suất)
- [Chỉnh prompt](#️-chỉnh-prompt)
- [Cấu trúc code](#-cấu-trúc-code)

---

## ✨ Tính năng chính

- **Bậc thang giá Top 5 → Top 1**: Top 5 là mẫu bình dân rẻ nhất, Top 1 là mẫu cao cấp nhất. Hạng càng cao càng xịn hơn rõ ràng, và Top 1 đáp ứng **tất cả** các tiêu chí mà Top 5 → Top 2 còn thiếu.
- **Script bám ảnh thật**: Gemini xem từng ảnh/video sản phẩm và viết lời bình đúng với ảnh đang hiện.
- **Review chân thật**: mỗi sản phẩm (kể cả Top 1) có 1 điểm "chê nhẹ" lặt vặt, bỏ qua được — tăng độ tin cậy mà không làm người xem mất hứng mua.
- **Giọng đọc có cảm xúc**: thẻ `[cười nhẹ]`, `[nhấn mạnh]`... tạo khoảng lặng tự nhiên.
- **Video kiểu CapCut**: zoom-in mượt theo từng frame, phụ đề hiện dần từng từ, chữ nhấn mạnh màu vàng, badge TOP/giá, hiệu ứng flash.
- **Mô tả YouTube tự động**: link sản phẩm, timestamp, hashtag ghép sẵn.

---

## 🧠 Kiến trúc

### 2 provider AI

| Provider | Free tier | Dùng cho |
|---|---|---|
| **Groq** | 30 request/phút, ~14.400/ngày | Mọi bước chỉ cần text: chọn ngách, lọc keyword, lọc sản phẩm, hook, CTA, metadata |
| **Gemini** (multimodal) | 5 request/phút, ~20/ngày | **Chỉ** bước viết lời bình từng sản phẩm (cần "nhìn" ảnh/video thật) — ~5 request/video |

### 2 giai đoạn

1. **Chọn lọc**: Ngách → Lọc keyword → Chọn đúng 5 sản phẩm theo bậc thang giá → **dừng lại** để bạn tự lấy ảnh/video chi tiết thật từ Shopee (thumbnail kết quả tìm kiếm không đủ chi tiết).
2. **Viết script + dựng video**: Gemini viết lời bình bám theo từng ảnh → audio giọng liền mạch → ghép video → ghi log vào DB.

---

## ⚠️ Lưu ý trước khi dùng

- **Quota Gemini free ~20 request/ngày** → khoảng 4 video/ngày. Bật billing tại Google AI Studio nếu cần nhiều hơn (chỉ vài cent/video).
- **Model có thể bị ngừng hỗ trợ đột ngột** (đã gặp với `gemini-2.5-flash` và `llama-3.3-70b-versatile`). Nếu gặp lỗi 404 *"model not found"*, đổi `GEMINI_MODEL` / `GROQ_MODEL` trong `.env` sang model đang hoạt động.
- **Edge-TTS không phải API chính thức** — thỉnh thoảng lỗi tạm thời, tool tự retry.
- **Link sản phẩm cần tự gắn tag affiliate** qua Shopee Affiliate Portal trước khi đăng — file scrape chỉ có `productUrl` gốc, tool sẽ cảnh báo khi thiếu.
- **Không nên auto-publish 100%** — kênh đăng hàng loạt không biên tập dễ vi phạm chính sách nội dung tái sử dụng của YouTube. Hãy xem lại `final_video.mp4` trước khi đăng.
- **Thời gian render**: zoom được dựng theo từng frame bằng OpenCV (~18 ms/frame). Mỗi sản phẩm có `video.mp4` thật sẽ tốn thêm thời gian ghép (chỉ chèn 1 câu/sản phẩm).

---

## 📦 Cài đặt

Yêu cầu: **Python 3.10+**, Windows (hoặc Linux/macOS — đổi lệnh kích hoạt venv tương ứng).

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Điền API key vào `.env`:

| Biến | Lấy ở đâu |
|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> |
| `GROQ_API_KEY` | <https://console.groq.com/keys> (miễn phí, không cần thẻ) |

> `opencv-python-headless` là tuỳ chọn nhưng nên cài (đã có trong `requirements.txt`) — thiếu thì zoom vẫn chạy nhưng render chậm hơn nhiều.

---

## 🖥 Giao diện web (Streamlit)

**Khuyên dùng** thay vì CLI:

```bash
venv\Scripts\activate
streamlit run app.py
```

Mở <http://localhost:8501>. Sidebar dùng để chọn/tạo một **run** (phiên làm việc). Các tab đi đúng theo luồng CLI nhưng thao tác bằng chuột:

- Upload file CSV/JSON đầu vào
- Xem/upload ảnh sản phẩm theo từng thư mục
- Sửa script ngay trên trình duyệt, nghe thử từng audio
- Sửa mô tả YouTube (title, link, timestamp, hashtag) trước khi đăng
- Xem trước và tải video
- Xem lịch sử và nhập số liệu hiệu suất

CLI vẫn dùng song song được (cùng đọc/ghi thư mục `data/`).

> Sau khi sửa code trong `src/`, cần **restart Streamlit** để nhận thay đổi. File prompt `prompts/*.md` thì được đọc lại mỗi lần chạy.

---

## 📥 Chuẩn bị dữ liệu đầu vào

Đặt vào `data/input/`:

- **Một hoặc nhiều file `.csv` từ khoá** (export từ tool nghiên cứu keyword)
  - Bắt buộc: cột `Keyword`
  - Nên có: `Search volume`, `Competition`, `Overall`, `Related score`
- **Một file `.json` sản phẩm Shopee**, dạng:

  ```json
  {
    "products": [
      { "name": "...", "price": 0, "imageUrl": "...", "productUrl": "...", "salesVolume": 0 }
    ]
  }
  ```

Nếu file thật có cấu trúc khác, chỉnh `src/ingestion.py`.

---

## 🚀 Chạy bằng CLI

### Bước A — Chọn 5 sản phẩm

```bash
python main.py
```

Đọc các file trong `data/input/`, chạy prompt 1-3, rồi dừng lại và hướng dẫn mở `03b_product_links.md`.

### Bước B — Lấy ảnh thật, rồi viết script + audio + video

1. Mở từng link Shopee trong `03b_product_links.md`.
2. Tải ảnh/video vào đúng thư mục `product_media/<rank>_<tên-sp>/`:
   - `main_images/` — ảnh chính
   - `description_images/` — ảnh mô tả
   - `video.mp4` — video sản phẩm (nếu có)
3. Sản phẩm nào không có ảnh có thể để trống — tool sẽ sinh prompt Google Flow trong `07_flow_prompts.md` để bạn tự tạo ảnh AI bù vào.
4. Chạy:

```bash
python main.py --script-only data/output/<run_id>
```

### Các lệnh khác

| Mục đích | Lệnh |
|---|---|
| Test nhanh không cần ảnh thật | `python main.py --auto-script` |
| Sửa tay `04_script.txt` rồi chạy lại audio + video (không tốn API) | `python main.py --media-only data/output/<run_id>` |
| Chỉ ghép lại video | `python main.py --assemble-only data/output/<run_id>` |
| Video dọc 1080x1920 cho Shorts | `python main.py --assemble-only data/output/<run_id> --vertical` |

**Tuỳ chọn thêm:**

| Flag | Ý nghĩa |
|---|---|
| `--voice vi-VN-NamMinhNeural` | Đổi sang giọng nam |
| `--rate +10%` | Đọc nhanh hơn |
| `--fps 24` | Đổi FPS video |
| `--font <path.ttf>` | Đổi font chữ |
| `--skip-media` | Dừng sau khi có script |
| `--skip-assembly` | Dừng sau khi có audio, chưa ghép video |

> 💡 Nếu chạy lại bước viết script cho một run cũ, hãy xoá `04_script_progress.json` trong thư mục run — nếu không, tool sẽ dùng lại lời bình đã sinh trước đó.

---

## 📂 Kết quả đầu ra

Tất cả nằm trong `data/output/<run_id>/`:

| File | Nội dung |
|---|---|
| `01_niche.json` | Ngách đã chọn |
| `02_keywords_filtered.json` | Từ khoá đã lọc |
| `03_products_selected.json` | 5 sản phẩm: hạng, phân khúc, tiêu chí đáp ứng/còn thiếu, điểm chê nhẹ |
| `03b_product_links.md` | Link từng sản phẩm để lấy ảnh/video |
| `04_script.txt` | Script — mỗi câu gắn 1 ảnh cụ thể + thẻ cảm xúc |
| `05_metadata.json` | Tiêu đề, hook mô tả, nhãn timestamp, tags, chữ thumbnail |
| `06_media_manifest.json` | Từng câu + ảnh + timing, dùng để ghép video |
| `07_flow_prompts.md` | Prompt Google Flow (chỉ khi có sản phẩm thiếu ảnh) |
| `08_youtube_description.txt` | Mô tả YouTube đầy đủ: mở đầu → link sản phẩm → timestamp → hashtag |
| `audio/*.mp3` | Mỗi đoạn một file, giọng liền mạch |
| `final_video.mp4` | Video hoàn chỉnh |

**Video `final_video.mp4` gồm:**

- Nền mờ phóng to từ chính ảnh đang hiện + ảnh sản phẩm nét ở giữa
- Badge **TOP** và **giá**
- Phụ đề hiện dần từng từ theo tiến độ đọc
- Chữ nhấn mạnh màu vàng viền đen (kiểu CapCut)
- Card "lý do chọn" ở câu đầu mỗi sản phẩm
- Zoom-in mượt theo từng frame, liên tục xuyên suốt khi nhiều câu dùng chung 1 ảnh
- Hiệu ứng flash sáng mỗi ~12 giây
- Video sản phẩm thật (nếu có) chèn vào câu dài nhất của sản phẩm đó, tắt tiếng

> ⚠️ Nhớ đổi link trong mô tả sang bản đã gắn tag affiliate trước khi đăng.

---

## 📊 Log lịch sử & hiệu suất

Mỗi lần ghép video xong, tool tự ghi vào `data/history.db` (SQLite): ngách, tiêu đề, danh sách sản phẩm, đường dẫn video.

Không có API tự động lấy YouTube Analytics / Shopee Affiliate, nên sau khi đăng video vài ngày, nhập số liệu thủ công:

```bash
python main.py --log-performance data/output/<run_id> --views 1500 --ctr 4.2 --clicks 30 --revenue 250000 --youtube-url https://youtu.be/xxxx
```

Xem toàn bộ lịch sử + hiệu suất:

```bash
python main.py --report
```

Dùng số liệu này để biết ngách/hook nào ra đơn tốt và ưu tiên làm lại kiểu tương tự.

---

## ✏️ Chỉnh prompt

Các prompt nằm trong `prompts/*.md`, dùng cú pháp `{{PLACEHOLDER}}` — sửa trực tiếp, không cần đụng code Python.

| Prompt | Ghi chú |
|---|---|
| `03_loc_san_pham.md` | Chọn 5 sản phẩm theo bậc thang giá. Code sắp lại hạng theo giá (Top 1 = đắt nhất) và cảnh báo nếu hạng trên kém tính năng hơn hạng dưới, hoặc Top 1 còn thiếu tiêu chí. |
| `04b_product_segment.md` | Prompt **duy nhất** nhận ảnh/video (Gemini). Yêu cầu đánh dấu `[[IMG:n]]` trước mỗi câu — **giữ nguyên định dạng này** vì `src/script_parser.py` và `src/video_assembly.py` dựa vào đó để khớp ảnh với câu. |
| `04d_highlight.md` | Sinh cụm chữ nhấn mạnh màu vàng (Groq, chia lô 20 câu/lần). Lô nào lỗi thì bỏ qua, không chặn audio/video. |
| `05_metadata.md` | Sinh title, tags, `hook_description`, `hook_label` / `outro_label` cho mô tả YouTube. |

**Một số tham số hay chỉnh:**

| Muốn chỉnh | Ở đâu |
|---|---|
| Số ảnh tối đa gửi Gemini mỗi sản phẩm (mặc định 6) | `_MAX_IMAGES_FOR_SCRIPT` — `src/product_links.py` |
| Độ dài khoảng lặng theo thẻ cảm xúc | `_PAUSE_MS_RULES` — `src/tts.py` |
| Màu chữ nhấn mạnh | `HIGHLIGHT_COLOR`, `HIGHLIGHT_STROKE` — `src/video_assembly.py` |
| Màu card "lý do chọn" | `INTRO_CARD_BG` — `src/video_assembly.py` |
| Biên độ / tốc độ zoom | `_ZOOM_MAX`, `_ZOOM_SPEED`, `_ZOOM_FOCUS_Y` — `src/video_assembly.py` |

> Thẻ cảm xúc (`[cười nhẹ]`, `[thở dài]`, `[nhấn mạnh]`, `[ngập ngừng]`, `[hào hứng]`) không đổi cao độ giọng (edge-tts không hỗ trợ), chỉ tạo khoảng lặng gần đúng vị trí thẻ.

> Tên sản phẩm trong link/timestamp luôn lấy nguyên văn từ `03_products_selected.json` — không để LLM tự đặt, vì đã gặp lỗi LLM gán nhầm tính năng sản phẩm A cho sản phẩm B.

---

## 🗂 Cấu trúc code

| File | Vai trò |
|---|---|
| `app.py` | Giao diện web Streamlit |
| `main.py` | CLI, điều phối toàn bộ luồng |
| `src/pipeline.py` | Chuỗi prompt giai đoạn 1-2, xếp hạng bậc thang giá, checkpoint resume khi hết quota |
| `src/gemini_client.py`, `src/groq_client.py` | Wrapper 2 provider, retry khi bị rate-limit |
| `src/ingestion.py`, `src/product_links.py` | Đọc CSV/JSON đầu vào, quản lý `product_media/` |
| `src/script_parser.py` | Tách `04_script.txt` thành đoạn/khối theo `[[IMG:n]]` |
| `src/tts.py` | Sinh audio — cảm xúc = khoảng lặng |
| `src/media_step.py` | Nối script parser + TTS + ảnh dự phòng + mô tả YouTube |
| `src/description.py` | Ghép `08_youtube_description.txt` từ metadata + timing thật |
| `src/video_assembly.py` | Ghép MP4 bằng MoviePy + Pillow + OpenCV (xem comment đầu file) |
| `src/database.py` | SQLite log lịch sử + hiệu suất |
