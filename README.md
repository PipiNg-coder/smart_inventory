# ShelfSense – một luồng duy nhất: giao diện → FastAPI → BigQuery

```
Trình duyệt (shelfsense.html trong Streamlit)
        │  POST /api/phieu   (một hàm duy nhất: sendShelfSlipToBigQuery)
        ▼
FastAPI (api.py → web_api.py → utils/phieu.py)   kiểm tra dữ liệu, chống ghi trùng
        │  ghi 3 bảng, ĐỌC LẠI để xác nhận đủ, rồi đọc tồn thật
        ▼
BigQuery (phieu_log, phieu_chi_tiet_log, stock_movement_live; v_ton_live là nguồn tồn)
```
BigQuery là nguồn sự thật: sau khi ghi thành công, giao diện hiển thị đúng **tồn do API đọc lại từ `v_ton_live`**, không tự cộng/trừ.

## Chạy trên máy
```powershell
# Terminal 1 – API
uvicorn api:app --host 127.0.0.1 --port 8000
# Terminal 2 – giao diện
streamlit run app.py
# Terminal 3 – kiểm tra
python kiem_tra_api.py 8504          # thay 8504 bằng cổng Streamlit của bạn
```

## Các luồng ghi phiếu (tất cả đi qua một hàm gửi duy nhất)
| Màn hình | Loại phiếu | Tồn |
|---|---|---|
| Nhập hàng; Nhận hàng theo đơn đặt | `NHAP` | + |
| Bán hàng (giỏ hàng) và form "Bán hàng" | `BAN` | − |
| Trả hàng | `TRA_HANG` | + |
| Hủy hàng – Hết date / Lỗi | `HUY` | − |
| Hủy hàng – Đổi luôn / Đổi hàng | `HUY` | + (quy tắc nằm ở `utils/phieu.py`, giao diện dùng cùng quy tắc) |
Luồng cũ `submitTransaction()` (chỉ sửa trong trình duyệt) giờ chuyển sang luồng chung; `receiveOrder` cũ đã ngừng dùng; `app.py` không còn chèn script gửi phiếu thứ hai.

## Chống ghi dở và ghi trùng
- Mỗi lần bấm xác nhận có một mã sự kiện; mất mạng/quá hạn rồi bấm lại dùng **đúng mã đó**, API không ghi trùng.
- Ghi lần lượt 3 bảng (bảng làm đổi tồn ghi cuối). Sau khi ghi, API **đọc lại** cả 3 bảng; thiếu bảng nào thì trả lỗi "ghi dở dang" (không báo thành công). Gửi lại đúng phiếu đó chỉ ghi phần còn thiếu.
- BigQuery load job không phải một giao dịch nguyên tử. Cách thay thế là `BEGIN TRANSACTION` + `INSERT` (cần dự án bật thanh toán, không dùng được ở BigQuery sandbox), hiện chưa dùng.

## Triển khai (Streamlit Cloud + FastAPI riêng, ví dụ Cloud Run)
1. Thư mục `utils/` của API cần có cả `bigquery_client.py` của bạn và `phieu.py` bản mới. Cloud Run gắn Service Account cho dịch vụ nên **không cần file JSON**; cấp quyền *BigQuery Data Editor* và *BigQuery Job User*.
2. Triển khai API:
   ```bash
   gcloud run deploy shelfsense-api --source . --region asia-southeast1 --allow-unauthenticated \
     --set-env-vars SHELFSENSE_ALLOWED_ORIGINS=https://TEN-APP.streamlit.app,SHELFSENSE_API_KEY=CHUOI_NGAU_NHIEN_DAI
   ```
3. Streamlit Cloud → Settings → Secrets (mẫu: `secrets.toml.example`): `[api] base_url = "https://…run.app"`, `key = "CHUOI_NGAU_NHIEN_DAI"`.
4. Kiểm tra: `python kiem_tra_api.py https://TEN-APP.streamlit.app https://…run.app`, rồi `python kiem_thu_phieu.py --sku SKU001 --api https://…run.app --key …`.

## Bảo mật – điều cần hiểu đúng
- **Khóa API nằm trong trang**, nên ai xem được trang đều đọc được khóa. Nó chỉ chặn người lạ không mở được app. Muốn an toàn thật cần giới hạn người dùng của chính app Streamlit (hoặc đăng nhập thật ở server API).
- Không đưa `service-account.json` vào GitHub hay JavaScript (`.gitignore` đã chặn `*.json` và `secrets.toml`).
- API chỉ chấp nhận trang từ domain khai báo trong `SHELFSENSE_ALLOWED_ORIGINS`.

## Kiểm thử
- `python kiem_thu_phieu.py --sku SKU001` – chạy thật từ API đến BigQuery: nhập +1, gửi lại 2 lần (không ghi trùng), hủy −1, vượt tồn bị từ chối; tồn cuối trở về như ban đầu.
- `python kiem_tra_api.py` – API chạy chưa, BigQuery/quyền, CORS, khóa API.

## Còn lại cần biết
- Lịch sử hiển thị trong giao diện vẫn là dữ liệu trình duyệt; tồn là số thật từ BigQuery khi tải lại và sau mỗi phiếu.
- Tồn live phản ánh ngay; số bán cho dự báo chỉ vào mô hình sau khi script 07 chạy.
