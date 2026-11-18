"""Trang nhập phiếu: ghi phiếu thật vào BigQuery (van_hanh_v2).
Script 07 sẽ đồng bộ phiếu sang mart_data_v2 để mô hình dự báo dùng.
Đặt file này ở thư mục pages/ cạnh app.py, mở bằng địa chỉ .../nhap_phieu
"""
import pandas as pd
import streamlit as st

from utils.phieu import tao_phieu

st.set_page_config(page_title="Nhập phiếu — ShelfSense", page_icon="🧾", layout="centered")
st.title("🧾 Nhập phiếu")
st.caption(
    "Phiếu được ghi vào BigQuery ngay và tồn kho live cập nhật tức thì. "
    "Số liệu cho mô hình dự báo chỉ được đồng bộ sau khi script 07 chạy (gom đến hết ngày hôm qua)."
)

LOAI = {"BAN": "Bán hàng", "NHAP": "Nhập hàng", "TRA_HANG": "Khách trả hàng", "HUY": "Hủy / hao hụt"}
loai = st.selectbox("Loại phiếu", list(LOAI), format_func=lambda k: LOAI[k])
c1, c2 = st.columns(2)
doi_tac = c1.text_input("Đối tác (khách / nhà cung cấp)")
nguoi = c2.text_input("Người thực hiện")
ly_do = st.text_input("Ghi chú / lý do")
la_thu = st.checkbox("Đây là phiếu thử (script 07 sẽ bỏ qua, không vào dữ liệu mô hình)", value=False)

st.markdown("**Danh sách món**")
bang = st.data_editor(
    pd.DataFrame(
        {
            "sku": pd.Series(dtype="str"),
            "so_luong": pd.Series(dtype="float"),
            "don_gia": pd.Series(dtype="float"),
        }
    ),
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "sku": st.column_config.TextColumn("SKU", required=True),
        "so_luong": st.column_config.NumberColumn("Số lượng", min_value=0.0, required=True),
        "don_gia": st.column_config.NumberColumn("Đơn giá (đ)", min_value=0.0, default=0.0),
    },
)

if st.button("Lưu phiếu", type="primary"):
    dong = [
        {
            "sku": r.sku,
            "so_luong": r.so_luong,
            "don_gia": 0.0 if pd.isna(r.don_gia) else r.don_gia,
        }
        for r in bang.itertuples(index=False)
        if isinstance(r.sku, str) and r.sku.strip()
    ]
    try:
        ma = tao_phieu(
            loai,
            dong,
            doi_tac=doi_tac,
            nguoi=nguoi.strip() or "web",
            la_phieu_thu=la_thu,
            ly_do=ly_do,
        )
        st.success(f"Đã lưu phiếu {ma}.")
        st.cache_data.clear()  # để trang chính đọc lại tồn kho mới
    except ValueError as e:  # lỗi nhập liệu: sai mã, hết tồn, số lượng <= 0...
        st.error(str(e))
    except Exception as e:  # lỗi ghi BigQuery
        st.error(f"Không ghi được lên BigQuery: {type(e).__name__}: {e}")
