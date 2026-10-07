# Auto Keyframe Pro V2 — Add-on cho Tool AutoCapcut V6.3

Bản nâng cấp của tính năng **4. Auto Keyframe**, chạy song song với tool gốc và
không sửa gì vào `Tool_AutoCapcut_V6.3.exe`.

## Cài & chạy

Máy đã có sẵn Python 3.11 nên chỉ cần **double-click `Chay_AutoKeyframePro.bat`**.
Không cần cài thêm thư viện nào (chỉ dùng thư viện chuẩn của Python).

Quy trình làm việc:

1. Dùng Tool AutoCapcut V6.3 như bình thường để sinh dự án CapCut.
2. **Đóng CapCut lại** (quan trọng — CapCut giữ draft trong bộ nhớ và sẽ ghi đè khi thoát).
3. Mở Auto Keyframe Pro → Quét → chọn dự án → chỉnh tuỳ chọn → **Áp dụng**.
4. Mở lại CapCut, bấm vào dự án để xem kết quả.

## Tính năng so với bản gốc

| | Tool V6.3 gốc | Auto Keyframe Pro V2 |
|---|---|---|
| Kiểu chuyển động | 5 kiểu | 8 kiểu (thêm Ken Burns Pro, Parallax 19:6, Breathing Slow) |
| Đường cong | `curveType: Line`, 2–4 điểm → chuyển động đều, máy móc | Ease In/Out bake sẵn, 3–21 điểm → có gia tốc, mượt |
| Lớp nền | không có | tự nhân bản ảnh làm nền phủ kín khung + Canvas Blur + alpha |
| Nền chuyển động | không có | zoom chậm 8% hoặc parallax ngược chiều lớp chính |
| Khung 19:6 | không có | letterbox 1920×606 + vignette + viền mờ |
| Hoàn tác | không có | backup tự động + nút Khôi phục bản gốc |

## Giải thích các tuỳ chọn

**Curve mượt Ease In/Out** — Tool gốc ghi keyframe với `curveType: "Line"`, tức là
ảnh chạy đều một tốc độ từ đầu đến cuối, mắt người nhận ra ngay là máy làm.
Bản Pro lấy mẫu nhiều điểm trên đường cong S (smoothstep) rồi ghi thẳng giá trị đã
tính vào keyframe, nên chuyển động khởi động chậm — nhanh dần ở giữa — hãm lại ở cuối,
giống hệt cách editor kéo tay. Cách bake này không phụ thuộc vào enum `curveType`
nào của CapCut nên chạy đúng trên mọi phiên bản.

**Lớp nền mờ** — Mỗi ảnh trên track chính được nhân bản xuống một track nằm dưới,
phóng to đúng hệ số phủ kín khung (tự tính từ kích thước thật của file ảnh, không tin
theo `width/height` trong draft vì tool gốc ghi cứng 1920×1080 cho mọi ảnh), hạ alpha
xuống 0.30 và bật **Canvas Blur** — tính năng blur gốc của CapCut, blur thật chứ không
phải chỉ giảm opacity.

**Nền chuyển động chậm** — Lớp nền được gắn keyframe zoom 100% → 108% trong suốt clip.
Nền đứng yên tuyệt đối cạnh lớp chính đang chuyển động là dấu hiệu dễ nhận của edit
nghiệp dư; chuyển động rất chậm này khiến khung hình "thở".

**Parallax 19:6** — Chọn kiểu này thì lớp chính trôi ngang một chiều còn lớp nền trôi
ngược chiều, tạo chiều sâu. Đây là kiểu hợp nhất với khung cực rộng 19:6.

**Khung hình 19:6** — Giữ nguyên canvas 1920×1080 và chèn một ảnh PNG phủ toàn timeline:
2 thanh đen cao 237px ở trên/dưới, chừa dải hình 1920×606 (đúng tỉ lệ 19:6), mép trong
của thanh được feather 14px cho mềm. Cách này để YouTube phát ở khung 16:9 quen thuộc,
không tự chèn thêm viền đen như khi upload file 19:6 thật.

**Vignette** — Một PNG phủ tối nhẹ hai mép dải hình, làm mắt dồn vào giữa và che chỗ
nền blur bị cắt cứng ở rìa.

## An toàn & hoàn tác

- Lần chạy đầu tiên, app lưu `draft_content.AKP_original.json` và
  `draft_meta_info.AKP_original.json` — bản gốc thật, không bao giờ bị ghi đè.
- Mỗi lần chạy còn tạo thêm một bản `draft_content.AKP_backup_<ngày giờ>.json` (giữ 5 bản gần nhất).
- Nút **Khôi phục bản gốc** trả dự án về đúng trạng thái trước khi chạy add-on.
- Chạy lại nhiều lần không bị chồng lớp: app ghi ID những gì nó tạo vào `akp_state.json`
  và gỡ sạch trước khi áp dụng lần mới.
- Mọi object JSON sinh ra đều được **clone từ object có thật trong chính draft đó**,
  nên luôn đúng schema của phiên bản CapCut đang cài.

## Lưu ý

- **Đừng di chuyển thư mục `AutoKeyframePro`** sau khi đã áp dụng: đường dẫn 2 file PNG
  trong `assets` được ghi thẳng vào draft, chuyển chỗ sẽ khiến CapCut báo thiếu media.
  Nếu buộc phải chuyển, chạy lại app một lần nữa để ghi lại đường dẫn mới.
- Luôn đóng CapCut trước khi bấm Áp dụng.
- Chọn đúng **Track ảnh chính** trong ô thứ nhất. App tự đoán track có nhiều ảnh alpha 1.0
  nhất, nhưng dự án nhiều lớp (như 0914 (2)) thì nên kiểm tra lại: mô tả mỗi track có ghi
  sẵn số ảnh, số lớp nền và số clip đã có keyframe.

## Cấu trúc

```
AutoKeyframePro/
├── Chay_AutoKeyframePro.bat     # double-click để chạy
├── AutoKeyframePro.py           # giao diện
├── akp_engine.py                # engine xử lý draft_content.json
├── assets/
│   ├── letterbox_19_6.png       # 2 thanh 19:6 + feather
│   └── vignette_19_6.png        # vignette 2 mép
└── README_AutoKeyframePro.md
```
