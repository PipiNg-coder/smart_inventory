# ShelfSense – BigQuery Demo (local)

## Kiến trúc
- `app.py` (Streamlit) đọc sản phẩm, tồn live, dự báo từ BigQuery.
- `api.py` (FastAPI) nhận phiếu từ trình duyệt.
- `web_api.py` kiểm tra dữ liệu và gọi `utils/phieu.py`.
- `utils/phieu.py` ghi vào đúng các bảng `van_hanh_v2.phieu_log`, `phieu_chi_tiet_log`, `stock_movement_live`.
- `shelfsense.html` là giao diện gốc, giữ nguyên thiết kế và ảnh nhúng.

## Cài đặt (Windows / VS Code)
Mở Terminal tại thư mục chứa `app.py`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Đặt Google credentials theo MỘT trong hai cách:
1. Tạo `.streamlit/secrets.toml` từ `.streamlit/secrets.toml.example`, điền đầy đủ Service Account JSON fields; hoặc
2. Thiết lập ADC bằng `gcloud auth application-default login`.

Service Account cần quyền đọc các bảng/view analytics và quyền ghi vào 3 bảng vận hành nêu trên. Không đưa khóa JSON vào HTML hoặc GitHub.

## Chạy ứng dụng
Mở **Terminal 1**:
```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```
Kiểm tra `http://127.0.0.1:8000/health` trả `{"ok":true,...}`.

Mở **Terminal 2**:
```powershell
streamlit run app.py
```
Mở `http://localhost:8501`.

## Luồng phiếu đã nối
- Nhập hàng → `NHAP`
- Bán hàng → `BAN`
- Trả hàng → `TRA_HANG`
- Hủy hàng → `HUY` (Hết date/Lỗi trừ tồn; Đổi luôn/Đổi hàng cộng tồn)

Sau khi API xác nhận ghi thành công, giao diện mới cập nhật tồn tại phiên trình duyệt. Tải lại app sẽ đọc tồn live từ BigQuery.

## Giới hạn quan trọng cần biết
- Đây là cấu hình demo chạy trên **cùng máy**. API chỉ bind `127.0.0.1`; không dùng cấu hình này để public Internet hoặc triển khai GitHub/Streamlit Cloud. Nếu public, phải bổ sung đăng nhập/xác thực thật ở server.
- `phieu.py` hiện ghi 3 bảng bằng 3 BigQuery load jobs nối tiếp, không phải một transaction nguyên tử. Nếu job giữa chừng lỗi, cần đối soát log trước khi gửi lại.
- Bản này nối nút tạo phiếu ở màn hình nghiệp vụ chính. Các luồng legacy khác (checkout/nhận đơn mua/kiểm kê) chưa được chuyển sang API này; không dùng chúng để ghi tồn thực.
- Lịch sử hiển thị trong giao diện hiện là dữ liệu phiên trình duyệt, chưa có endpoint đọc lịch sử phiếu từ BigQuery. Nguồn tồn live khi tải lại vẫn lấy từ BigQuery.
- Chưa chạy kiểm thử end-to-end với project BigQuery thật trong môi trường tạo file này; cần chạy thử mỗi loại phiếu với SKU test.
