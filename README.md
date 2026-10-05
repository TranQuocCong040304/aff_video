# Auto Video Affiliate — Bước 1-4 hoàn chỉnh

Tool tự động dựng video review affiliate Shopee: chọn sản phẩm → viết script bám ảnh/video thật →
audio có cảm xúc → ghép video → log lịch sử/hiệu suất.

## Kiến trúc 2 provider + 2 giai đoạn

- **Groq** (free, rộng — 30 request/phút, ~14.400/ngày): mọi bước chỉ cần text — chọn ngách, lọc
  keyword, lọc sản phẩm, hook, CTA, metadata.
- **Gemini** (multimodal, free tier hẹp — 20 request/ngày, 5/phút): CHỈ bước viết lời bình từng
  sản phẩm, vì đây là bước duy nhất cần "nhìn" ảnh/video thật — 1 video chỉ tốn ~5 request Gemini.

**Giai đoạn 1** (chọn lọc): Ngách → Lọc keyword → Lọc ĐÚNG 5 sản phẩm → dừng lại, xuất link để bạn
tự lấy ảnh/video chi tiết thật từ Shopee (search-result thumbnail không đủ chi tiết).
**Giai đoạn 2** (viết script + dựng video): Gemini viết lời bình bám theo TỪNG ảnh cụ thể (không
chỉ nói chung chung) → audio giọng liền mạch có khoảng lặng theo cảm xúc → ghép video → log DB.

## ⚠️ Rủi ro cần biết trước khi dùng thật

- **Quota Gemini free tier ~20 request/ngày** → khoảng 4 video/ngày với thiết kế hiện tại. Bật
  billing tại Google AI Studio nếu cần nhanh hơn (rất rẻ, vài cent/video).
- **Model Gemini/Groq có thể ngừng hỗ trợ đột ngột** — đã gặp thật (`gemini-2.5-flash` bị deprecate
  giữa chừng, `llama-3.3-70b-versatile` trên Groq cũng vậy) — nếu lỗi 404 "model not found", đổi
  `GEMINI_MODEL`/`GROQ_MODEL` trong `.env` sang model hiện có (`python -c` liệt kê model Groq khả
  dụng, xem code trong `src/groq_client.py`).
- **Edge-TTS không phải API chính thức** — thỉnh thoảng báo lỗi tạm thời, tool tự retry.
- **Link sản phẩm cần tự gắn tag affiliate** qua Shopee Affiliate Portal trước khi đăng — file
  scrape chỉ có `productUrl` gốc, tool sẽ cảnh báo khi phát hiện thiếu.
- **Không nên auto-publish 100%** — kênh dựng hàng loạt không biên tập có nguy cơ vi phạm chính
  sách nội dung tái sử dụng của YouTube. `final_video.mp4` là điểm dừng kiểm duyệt bắt buộc.
- **Render video mất khá lâu** (~1.5-2 lần thời lượng video, do hiệu ứng zoom-in mỗi câu dựng nhiều
  ảnh tĩnh nối tiếp) — vẫn nhanh hơn animate thật từng frame rất nhiều lần (xem comment đầu
  `src/video_assembly.py`). Nếu TẤT CẢ sản phẩm đều có video thật trong `product_media/`, thời
  gian ghép có thể tăng thêm ~5-10 phút (mỗi đoạn video chèn vào tốn nhiều thời gian ghép hơn
  ảnh tĩnh) — vẫn chỉ chèn 1 câu/sản phẩm, không phải toàn bộ.

## Cài đặt

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Điền vào `.env`: `GEMINI_API_KEY` (https://aistudio.google.com/apikey) và `GROQ_API_KEY`
(https://console.groq.com/keys, miễn phí, không cần thẻ).

## Giao diện web (Streamlit) — khuyên dùng thay vì CLI

```bash
venv\Scripts\activate
streamlit run app.py
```

Mở `http://localhost:8501`. Sidebar để chọn/tạo 1 "run" (phiên làm việc); 6 tab tương ứng
đúng luồng CLI bên dưới nhưng thao tác bằng chuột — upload CSV/JSON, xem/upload ảnh sản phẩm
theo từng thư mục, sửa script ngay trên trình duyệt, nghe thử từng audio, sửa mô tả YouTube (title/
link/timestamp/hashtag đã ghép sẵn) trước khi copy đi đăng, xem trước + tải video, xem bảng lịch sử
và nhập số liệu hiệu suất — không cần nhớ lệnh/flag nào cả. Phần CLI dưới đây vẫn dùng được song
song (cùng đọc/ghi chung `data/`), phù hợp khi muốn chạy tự động theo script.

## Chuẩn bị dữ liệu đầu vào

Đặt vào `data/input/`:

- **1 hoặc nhiều file `.csv` từ khóa** (export từ tool nghiên cứu keyword). Bắt buộc có cột
  `Keyword`; `Search volume`, `Competition`, `Overall`, `Related score` tùy chọn nhưng nên có.
- **1 file `.json` sản phẩm Shopee** dạng `{"products": [{"name","price","imageUrl","productUrl",
  "salesVolume",...}]}`.

Nếu file thật có cấu trúc khác, chỉnh `src/ingestion.py`.

## Chạy — luồng đầy đủ (2 lệnh)

**Bước A — chọn 5 sản phẩm:**

```bash
python main.py
```

Tự đọc `.csv`/`.json` trong `data/input/`, chạy Prompt 1-3, dừng lại và in hướng dẫn xem
`03b_product_links.md`.

**Bước B — lấy ảnh thật rồi viết script + audio + video:**

Vào từng link Shopee trong `03b_product_links.md`, tải ảnh chính (`main_images/`), ảnh mô tả
(`description_images/`), video sản phẩm nếu có — thả vào đúng thư mục
`product_media/<rank>_<tên-sp>/`. Có thể để trống nếu sản phẩm nào không có ảnh riêng (tool tự
sinh gợi ý prompt Google Flow trong `07_flow_prompts.md` để bạn tự tạo ảnh AI bù vào, vì Flow
không có API miễn phí để tool tự gọi). Xong thì:

```bash
python main.py --script-only data/output/<run_id>
```

Kết quả trong `data/output/<run_id>/`:

- `01_niche.json`, `02_keywords_filtered.json`, `03_products_selected.json` — output Giai đoạn 1.
- `04_script.txt` — script, mỗi câu mô tả 1 ảnh cụ thể (đánh dấu `[[IMG:n]]` nội bộ) + thẻ cảm xúc.
- `05_metadata.json` — tiêu đề/hook mô tả/nhãn timestamp mở-kết/tags/thumbnail.
  `07_flow_prompts.md` — nếu có sản phẩm thiếu ảnh.
- `audio/*.mp3` — mỗi sản phẩm 1 file, giọng liền mạch (không đổi "người" giữa các câu).
- `06_media_manifest.json` — "beats": từng câu + ảnh cụ thể + timing, dùng để ghép video.
- `08_youtube_description.txt` — mô tả YouTube ĐẦY ĐỦ, ghép tự động SAU khi có audio (cần thời
  lượng thật để tính mốc thời gian): đoạn mở đầu → link từng sản phẩm (đúng thứ tự trong video) →
  timestamp từng đoạn → nhắc like/subscribe → hashtag. Dán thẳng vào ô mô tả khi đăng — nhớ đổi
  link sang bản đã gắn tag affiliate trước.
- `final_video.mp4` — nền mờ phóng to từ chính ảnh đang hiện + ảnh nét ở giữa + badge TOP/giá +
  phụ đề hiện DẦN từng từ theo tiến độ đọc trong câu (không hiện trọn câu ngay từ đầu — xấp xỉ
  tuyến tính theo số từ vì edge-tts không cho timing từng từ chính xác) + chữ nhấn mạnh màu vàng
  viền đen đè trên phụ đề (kiểu CapCut, xem bên dưới) + card lý do chọn ở câu đầu mỗi sản phẩm +
  zoom-in mượt mỗi câu (liên tục xuyên suốt nếu nhiều câu tả chung 1 ảnh, không giật lùi) + hiệu
  ứng flash sáng mỗi ~12s. Sản phẩm nào có
  `video.mp4` thật trong `product_media/` sẽ được thay 1 câu (dài nhất) bằng ĐÚNG đoạn video đó,
  tắt tiếng, cắt vừa khít thời lượng câu — không kéo dài video tổng, nhưng ghép LÂU HƠN nhiều
  (đã đo: ~10 lần/giây so với ảnh tĩnh) nên chỉ dùng có chọn lọc, không phải mọi câu.

**Test nhanh không cần ảnh thật** (viết script chỉ dựa tên/giá, bỏ qua bước dừng):

```bash
python main.py --auto-script
```

**Sửa tay script rồi chỉ chạy lại audio+video** (không tốn API Gemini/Groq):

```bash
# ... sửa data/output/<run_id>/04_script.txt ...
python main.py --media-only data/output/<run_id>
```

**Chỉ ghép lại video** (đổi khung hình/font, dùng audio/ảnh đã có sẵn):

```bash
python main.py --assemble-only data/output/<run_id>
python main.py --assemble-only data/output/<run_id> --vertical   # dọc 1080x1920 cho Shorts
```

Tùy chọn khác: `--voice vi-VN-NamMinhNeural` (giọng nam), `--rate +10%` (đọc nhanh hơn),
`--fps 24`, `--font <path.ttf>`, `--skip-media` (dừng sau script), `--skip-assembly` (dừng sau
audio, chưa ghép video).

## Bước 4 — Log lịch sử & đo hiệu suất

Mỗi lần ghép video xong, tool tự ghi vào `data/history.db` (SQLite): ngách, tiêu đề, danh sách sản
phẩm, đường dẫn video. **Không có API tự động lấy YouTube Analytics / Shopee Affiliate** (cần OAuth
+ đăng ký app riêng, ngoài phạm vi tool cá nhân) — sau khi đăng video và theo dõi vài ngày, tự nhập:

```bash
python main.py --log-performance data/output/<run_id> --views 1500 --ctr 4.2 --clicks 30 --revenue 250000 --youtube-url https://youtu.be/xxxx
```

Xem toàn bộ lịch sử + hiệu suất:

```bash
python main.py --report
```

Dùng số liệu này để nhận ra ngách/hook nào ra đơn tốt, ưu tiên làm lại kiểu tương tự — đây là phần
quyết định doanh thu mà bản kế hoạch ban đầu còn thiếu.

## Chỉnh prompt

`prompts/*.md`, cú pháp `{{PLACEHOLDER}}` — sửa trực tiếp, không cần đụng code Python.

- `04b_product_segment.md` là prompt DUY NHẤT nhận ảnh/video đính kèm (multimodal, gọi qua Gemini).
  Yêu cầu Gemini đánh dấu `[[IMG:n]]` trước mỗi câu tương ứng với ảnh thứ n — nếu sửa, giữ nguyên
  yêu cầu định dạng này vì `src/script_parser.py` và `src/video_assembly.py` dựa vào đó để đồng bộ
  đúng ảnh với đúng câu.
- Tối đa **6 ảnh/sản phẩm** được gửi cho Gemini (ưu tiên `main_images/` trước) — chỉnh
  `_MAX_IMAGES_FOR_SCRIPT` trong `src/product_links.py` nếu muốn nhiều/ít hơn (nhiều ảnh hơn = script
  dài hơn = video dài hơn).
- Thẻ cảm xúc `[cười nhẹ]`, `[thở dài]`, `[nhấn mạnh]`, `[ngập ngừng]`, `[hào hứng]` không đổi
  pitch/tốc độ giọng (edge-tts không hỗ trợ nhiều mức prosody trong 1 lệnh gọi) — chỉ tạo khoảng
  lặng gần đúng vị trí thẻ. Chỉnh độ dài khoảng lặng/từ khóa nhận diện trong `_PAUSE_MS_RULES` ở
  `src/tts.py`.
- `04d_highlight.md` sinh cụm từ nhấn mạnh (chữ to màu vàng đè lên phụ đề, kiểu CapCut) — vài lệnh
  gọi Groq cho cả video, chia lô 20 câu/lần để JSON không bị cắt ngắn (xem `src/media_step.py:
  _extract_highlights`), lô nào lỗi thì bỏ qua trang trí lô đó chứ không chặn audio/video. Đổi
  màu/font size ở `HIGHLIGHT_COLOR`/`HIGHLIGHT_STROKE` trong `src/video_assembly.py`. Card "vì sao
  chọn" ở câu đầu mỗi sản phẩm dùng lại `reason_selected` có sẵn, không tốn thêm request nào — đổi
  màu ở `INTRO_CARD_BG`.
- `05_metadata.md` (Groq, chạy 1 lần ở cuối Giai đoạn 2) ngoài title/tags còn sinh `hook_description`
  (đoạn mở đầu mô tả YouTube) và `hook_label`/`outro_label` (nhãn timestamp mở đầu/kết).
  `src/description.py: build_youtube_description()` ghép các phần này với thời lượng THẬT đọc từ
  `06_media_manifest.json` (chỉ biết sau khi có audio) thành `08_youtube_description.txt` — gọi tự
  động ở cuối `run_media_step()`, không tốn thêm request nào (thuần code). Tên sản phẩm trong
  link/timestamp LUÔN lấy nguyên văn từ `03_products_selected.json`, tagline trong timestamp trích
  thẳng từ `reason_selected` — KHÔNG để LLM tự đặt biệt danh/mô tả riêng (đã gặp thật: LLM gán nhầm
  tính năng sản phẩm A cho timestamp của sản phẩm B khi được viết tự do).

## Kiến trúc code (tham khảo nhanh)

| File | Vai trò |
|---|---|
| `main.py` | CLI, điều phối toàn bộ luồng |
| `src/pipeline.py` | Chuỗi prompt Giai đoạn 1-2, checkpoint resume khi hết quota giữa chừng |
| `src/gemini_client.py` / `src/groq_client.py` | Wrapper 2 provider, retry rate-limit |
| `src/ingestion.py` / `src/product_links.py` | Đọc CSV/JSON đầu vào, quản lý `product_media/` |
| `src/script_parser.py` | Tách `04_script.txt` thành đoạn/khối theo `[[IMG:n]]` |
| `src/tts.py` | Sinh audio — 1 lệnh gọi/block, cảm xúc = khoảng lặng |
| `src/media_step.py` | Nối script_parser + tts + tải ảnh thumbnail dự phòng + gọi description.py |
| `src/description.py` | Ghép `08_youtube_description.txt` từ metadata + timing thật (sau khi có audio) |
| `src/video_assembly.py` | Ghép MP4 bằng MoviePy — xem comment đầu file về các hiệu ứng đã thử và bỏ vì quá chậm |
| `src/database.py` | SQLite log lịch sử + hiệu suất (Bước 4) |

Xem chi tiết đầy đủ trong bản kế hoạch tối ưu (artifact đã publish trong hội thoại).
#   a f f _ v i d e o  
 