"""ShelfSense – Smart Inventory (Streamlit + BigQuery).

Streamlit đọc dữ liệu thật từ BigQuery:
  - danh mục sản phẩm      : mart_data_v2.dim_product (hơn 10.000 món)
  - dự báo + cảnh báo      : mart_data_v2.canh_bao_ton_kho_v2 (chỉ các món có mô hình, hiện là 150 món)
  - tồn kho theo thời gian thực: van_hanh_v2.v_ton_live (đã cộng phiếu chưa đồng bộ)
  - dự báo THEO NGÀY (có tính dịp lễ) : mart_data_v2.du_doan_ngay_le_v2 (cách dự báo LightGBM/SBA ở du_doan_ngay_v2)
  - dịp lễ / top mã tăng doanh thu    : mart_data_v2.du_doan_theo_dip_v2
  - doanh thu thực tế theo ngày       : mart_data_v2.fact_sales
  - hình ảnh sản phẩm                 : cleaned_data.product.image_urls (URL KiotViet, lấy ảnh đầu tiên)
rồi chèn vào file shelfsense.html và nhúng toàn màn hình.

Lưu ý:
  - Trang Danh mục và Tồn kho hiển thị TOÀN BỘ món trong dim_product (kể cả tồn bằng 0 hoặc tồn âm);
    shelfsense.html phân trang để không nặng trình duyệt.
  - Món KHÔNG có mô hình dự báo được đánh dấu hasModel = False (dự báo/tồn tối thiểu = 0 chỉ là giá trị
    giữ chỗ, KHÔNG có nghĩa là nhu cầu bằng 0). Trang dự báo và cảnh báo chỉ dùng các món có mô hình.
  - sold90 = món có bán trong 90 ngày cuối của dữ liệu (dùng để đếm "hết hàng" cho món đang bán).
  - Thêm ?refresh=1 vào địa chỉ web để xóa bộ nhớ đệm và đọc lại BigQuery ngay.
"""

from datetime import date
from pathlib import Path
import os
import hashlib
import json
import re
import traceback
from html import escape

import pandas as pd
import streamlit as st

from utils.bigquery_client import PROJECT_ID
from utils.data_loader import load_data

HTML_FILE = Path(__file__).parent / "shelfsense.html"
TUOI_DU_BAO_TOI_DA = 7  # dự báo bắt đầu cách hôm nay quá số ngày này thì báo "dự báo đã cũ"

# Toàn bộ danh mục lấy từ dim_product. Món có mô hình thì ghép thêm dự báo/cảnh báo.
# KHÔNG dùng COALESCE(..., 0): NULL nghĩa là "không có dự báo", khác với dự báo bằng 0.
SQL = f"""
SELECT d.sku, d.ten, d.nhom, d.gia_ban, d.gia_von, d.ma_nha_cung_cap, img.anh,
       (c.sku IS NOT NULL) AS co_mo_hinh,
       v.ton_live, c.ton_hien_tai, c.hang_dang_ve,
       c.ton_toi_thieu, c.ton_toi_da,
       c.du_bao_7, c.du_bao_30,
       c.muc_canh_bao, c.nen_nhap_goi_y, c.nen_nhap_du_30_ngay, c.so_ngay_ton_kho,
       c.dip_trong_7, c.dip_trong_30, c.tien_dong_von,
       c.la_top50, c.nhom_abcxyz, c.so_luong_nen_xa,
       c.doanh_thu_7_co_le, c.doanh_thu_30_co_le,
       (b.sku IS NOT NULL) AS co_ban_90_ngay
FROM `{PROJECT_ID}.mart_data_v2.dim_product` d
LEFT JOIN `{PROJECT_ID}.mart_data_v2.canh_bao_ton_kho_v2` c
  ON UPPER(TRIM(c.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN `{PROJECT_ID}.van_hanh_v2.v_ton_live` v
  ON UPPER(TRIM(v.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN (
  SELECT DISTINCT sku
  FROM `{PROJECT_ID}.mart_data_v2.fact_sales`
  WHERE ngay > DATE_SUB((SELECT MAX(ngay) FROM `{PROJECT_ID}.mart_data_v2.fact_sales`), INTERVAL 90 DAY)
) b ON UPPER(TRIM(b.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN (
  SELECT product_id, ANY_VALUE(TRIM(SPLIT(image_urls, ',')[SAFE_OFFSET(0)])) AS anh
  FROM `{PROJECT_ID}.cleaned_data.product`
  WHERE image_urls IS NOT NULL AND TRIM(image_urls) LIKE 'http%'
  GROUP BY product_id
) img ON UPPER(TRIM(img.product_id)) = UPPER(TRIM(d.sku))
ORDER BY d.nhom, d.ten
"""

# Dự phòng: nếu view tồn live lỗi (thiếu quyền, view chưa có...) thì đọc tồn đã đồng bộ từ ton_hien_tai.
SQL_DU_PHONG = f"""
SELECT d.sku, d.ten, d.nhom, d.gia_ban, d.gia_von, d.ma_nha_cung_cap, img.anh,
       (c.sku IS NOT NULL) AS co_mo_hinh,
       v.ton_cuoi_ngay AS ton_live, c.ton_hien_tai, c.hang_dang_ve,
       c.ton_toi_thieu, c.ton_toi_da,
       c.du_bao_7, c.du_bao_30,
       c.muc_canh_bao, c.nen_nhap_goi_y, c.nen_nhap_du_30_ngay, c.so_ngay_ton_kho,
       c.dip_trong_7, c.dip_trong_30, c.tien_dong_von,
       c.la_top50, c.nhom_abcxyz, c.so_luong_nen_xa,
       c.doanh_thu_7_co_le, c.doanh_thu_30_co_le,
       (b.sku IS NOT NULL) AS co_ban_90_ngay
FROM `{PROJECT_ID}.mart_data_v2.dim_product` d
LEFT JOIN `{PROJECT_ID}.mart_data_v2.canh_bao_ton_kho_v2` c
  ON UPPER(TRIM(c.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN `{PROJECT_ID}.mart_data_v2.ton_hien_tai` v
  ON UPPER(TRIM(v.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN (
  SELECT DISTINCT sku
  FROM `{PROJECT_ID}.mart_data_v2.fact_sales`
  WHERE ngay > DATE_SUB((SELECT MAX(ngay) FROM `{PROJECT_ID}.mart_data_v2.fact_sales`), INTERVAL 90 DAY)
) b ON UPPER(TRIM(b.sku)) = UPPER(TRIM(d.sku))
LEFT JOIN (
  SELECT product_id, ANY_VALUE(TRIM(SPLIT(image_urls, ',')[SAFE_OFFSET(0)])) AS anh
  FROM `{PROJECT_ID}.cleaned_data.product`
  WHERE image_urls IS NOT NULL AND TRIM(image_urls) LIKE 'http%'
  GROUP BY product_id
) img ON UPPER(TRIM(img.product_id)) = UPPER(TRIM(d.sku))
ORDER BY d.nhom, d.ten
"""

SQL_META = f"""
SELECT CAST(MIN(ngay_du_bao) AS STRING) AS ngay_du_bao, COUNT(DISTINCT sku) AS so_mo_hinh
FROM `{PROJECT_ID}.mart_data_v2.du_doan_v2`
"""

# Cách dự báo của từng mã (LightGBM hay SBA...), khoảng ngày dự báo, ngày chạy, ngày bán cuối của dữ liệu.
SQL_MODEL = f"""
SELECT cach_du_doan AS cach, COUNT(DISTINCT sku) AS so_sku,
       CAST(MIN(ngay) AS STRING) AS tu, CAST(MAX(ngay) AS STRING) AS den,
       CAST(MAX(ngay_chay) AS STRING) AS ngay_chay,
       (SELECT CAST(MAX(ngay) AS STRING) FROM `{PROJECT_ID}.mart_data_v2.fact_sales`) AS ngay_ban_cuoi
FROM `{PROJECT_ID}.mart_data_v2.du_doan_ngay_v2`
GROUP BY cach_du_doan
ORDER BY so_sku DESC
"""

# Độ chính xác từng cách dự báo (WAPE càng thấp càng tốt).
SQL_SCORE = f"""
SELECT Cach AS cach, WAPE_tuan, WAPE, R2_tuan, MAE, CAST(ngay_chay AS STRING) AS ngay_chay
FROM `{PROJECT_ID}.mart_data_v2.diem_so_mo_hinh_v2`
ORDER BY WAPE_tuan
"""

# Nhà cung cấp + số sản phẩm của từng nhà cung cấp (lấy từ dim_product.ma_nha_cung_cap)
# ---- Biểu đồ "doanh thu thực tế + dự báo theo ngày, có tính dịp lễ" (trang Tổng quan dự báo) ----
# Thực tế : mart_data_v2.fact_sales (số bán x giá bán trung bình), chỉ các mã có mô hình, 30 ngày cuối của dữ liệu.
SQL_SERIES_ACTUAL = f"""
SELECT CAST(ngay AS STRING) AS ngay, ROUND(SUM(so_luong_ban * gia_tb)) AS doanh_thu
FROM `{PROJECT_ID}.mart_data_v2.fact_sales`
WHERE sku IN (SELECT sku FROM `{PROJECT_ID}.mart_data_v2.du_doan_v2`)
  AND ngay > DATE_SUB((SELECT MAX(ngay) FROM `{PROJECT_ID}.mart_data_v2.fact_sales`), INTERVAL 30 DAY)
GROUP BY ngay
ORDER BY ngay
"""

# Dự báo theo ngày (30 ngày), đã nhân hệ số dịp lễ: mart_data_v2.du_doan_ngay_le_v2
# (dt_nen = doanh thu dự báo nền, dt_co_le = có tính lễ; ten_dip rỗng nếu ngày đó không chịu ảnh hưởng của lễ).
SQL_SERIES_FORECAST = f"""
SELECT CAST(ngay AS STRING) AS ngay, ROUND(SUM(dt_co_le)) AS dt_co_le, ROUND(SUM(dt_nen)) AS dt_nen,
       MAX(NULLIF(TRIM(ten_dip), '')) AS dip
FROM `{PROJECT_ID}.mart_data_v2.du_doan_ngay_le_v2`
GROUP BY ngay
ORDER BY ngay
"""

# Các dịp lễ nằm trong kỳ dự báo (vùng cam trên biểu đồ): mart_data_v2.du_doan_theo_dip_v2
SQL_DIP = f"""
SELECT ten_dip, CAST(ngay_le AS STRING) AS ngay_le, CAST(ngay_bat_dau AS STRING) AS ngay_bat_dau,
       so_ngay_anh_huong, SAFE_DIVIDE(SUM(doanh_thu_co_le), SUM(doanh_thu_nen)) AS he_so
FROM `{PROJECT_ID}.mart_data_v2.du_doan_theo_dip_v2`
GROUP BY ten_dip, ngay_le, ngay_bat_dau, so_ngay_anh_huong
ORDER BY ngay_le
"""

# Top 10 mã được dịp lễ làm tăng doanh thu dự báo nhiều nhất.
SQL_TOP_LE = f"""
SELECT sku, ten_dip, du_bao_nen, du_bao_co_le, he_so, chenh_lech_doanh_thu, do_tin_cay
FROM `{PROJECT_ID}.mart_data_v2.du_doan_theo_dip_v2`
WHERE chenh_lech_doanh_thu > 0
ORDER BY chenh_lech_doanh_thu DESC
LIMIT 10
"""


# Doanh thu theo ngày của TOÀN BỘ mã hàng (35 ngày cuối của dữ liệu) cho biểu đồ Tổng quan.
SQL_DOANH_THU_NGAY = f"""
SELECT ngay, SUM(so_luong_ban * gia_tb) AS doanh_thu
FROM `{PROJECT_ID}.mart_data_v2.fact_sales`
WHERE ngay > DATE_SUB((SELECT MAX(ngay) FROM `{PROJECT_ID}.mart_data_v2.fact_sales`), INTERVAL 35 DAY)
GROUP BY ngay
ORDER BY ngay
"""

# Thời điểm đồng bộ phiếu thành công gần nhất (để không cộng đôi hóa đơn đã ghi lên BigQuery).
SQL_DONG_BO_LUC = f"""
SELECT MAX(thoi_diem_chay) AS luc
FROM `{PROJECT_ID}.van_hanh_v2.lan_chay_mo_hinh`
WHERE loai = 'DONG_BO' AND trang_thai = 'THANH_CONG'
"""

# Doanh thu dự báo THEO NGÀY: tổng (số lượng dự báo x giá bán) của mọi mã trong du_doan_ngay_v2, gom theo ngày
# (cho đường nét đứt ở biểu đồ "Doanh thu thực tế và dự báo").
SQL_DOANH_THU_DU_BAO_NGAY = f"""
WITH gia AS (
  SELECT UPPER(TRIM(sku)) AS sku, ANY_VALUE(gia_ban) AS gia_ban
  FROM `{PROJECT_ID}.mart_data_v2.dim_product`
  GROUP BY 1
)
SELECT d.ngay, SUM(d.so_luong_du_doan * IFNULL(g.gia_ban, 0)) AS doanh_thu
FROM `{PROJECT_ID}.mart_data_v2.du_doan_ngay_v2` d
LEFT JOIN gia g ON g.sku = UPPER(TRIM(d.sku))
GROUP BY d.ngay
ORDER BY d.ngay
"""

# Top 10 mã bán nhiều nhất trong 7 / 30 ngày cuối của dữ liệu (cho khung "Top 10 hàng bán chạy" ở Tổng quan).
SQL_TOP_SOLD = f"""
WITH mx AS (SELECT MAX(ngay) AS d FROM `{PROJECT_ID}.mart_data_v2.fact_sales`)
SELECT ky, sku, qty FROM (
  SELECT ky, sku, qty, ROW_NUMBER() OVER (PARTITION BY ky ORDER BY qty DESC) AS rn
  FROM (
    SELECT k AS ky, f.sku, SUM(f.so_luong_ban) AS qty
    FROM `{PROJECT_ID}.mart_data_v2.fact_sales` f
    CROSS JOIN mx
    CROSS JOIN UNNEST([7, 30]) AS k
    WHERE f.ngay > DATE_SUB(mx.d, INTERVAL k DAY)
    GROUP BY k, f.sku
    HAVING SUM(f.so_luong_ban) > 0
  )
)
WHERE rn <= 10
ORDER BY ky, qty DESC
"""


SQL_NCC = f"""
SELECT s.supplier_id, s.supplier_name, s.phone, s.lead_time_days, s.status,
       COUNT(d.sku) AS so_san_pham
FROM `{PROJECT_ID}.cleaned_data.supplier` s
LEFT JOIN `{PROJECT_ID}.mart_data_v2.dim_product` d
  ON d.ma_nha_cung_cap = s.supplier_id
GROUP BY s.supplier_id, s.supplier_name, s.phone, s.lead_time_days, s.status
ORDER BY so_san_pham DESC, s.supplier_name
"""

# Chạy SAU các script của shelfsense.html: thay danh sách nhà cung cấp mẫu bằng dữ liệu BigQuery.
# Mọi bước đều có kiểm tra; thiếu hàm/biến nào thì bỏ qua, trang cũ vẫn chạy bình thường.
NCC_SCRIPT = r"""<script>
(function(){
  try{
    var B=window.__SHELFSENSE__||{},L=B.suppliers;
    if(!L||!L.length)return;
    if(typeof state==='undefined'||typeof suppliers!=='function'||typeof pageHeader!=='function'||typeof card!=='function'||typeof table!=='function'||typeof esc!=='function')return;
    state.suppliers=L.map(function(s){return {id:s.id,name:s.name,phone:s.phone||'—',leadTime:s.leadTime,count:s.count,status:s.status}});
    var orig=suppliers;
    suppliers=function(){
      if(current.sub==='vendors'){
        var rows=state.suppliers.map(function(s){
          return '<tr><td>'+esc(s.id)+'</td><td><b>'+esc(s.name)+'</b></td><td>'+esc(s.phone)+'</td><td>'+(s.leadTime==null?'—':esc(s.leadTime)+' ngày')+'</td><td>'+(s.count==null?'0':esc(s.count))+'</td></tr>';
        });
        var H=['Mã','Tên nhà cung cấp','Điện thoại','Số ngày chờ hàng','Số sản phẩm'];
        var T=(typeof paginatedTableV4==='function')?paginatedTableV4('vendors',H,rows,20).replace(/ sản phẩm<\/span>/,' nhà cung cấp</span>'):table(H,rows);
        return pageHeader('ĐỐI TÁC','Nhà cung cấp','Danh sách nhà cung cấp từ BigQuery ('+state.suppliers.length+' nhà cung cấp).')+card('Danh sách nhà cung cấp',T);
      }
      return orig();
    };
    if(current&&current.page==='suppliers'&&typeof render==='function')render();
  }catch(e){console.warn('Nhà cung cấp từ BigQuery:',e)}
})();
</script>"""

DEMO_ORDERS = (
    "invoices:[{id:'HD000',date:'2026-11-08',items:[{sku:'SKU002',qty:2,price:18000}],total:36000}],"
    "movements:[{id:'MV001',date:'2026-11-08',sku:'SKU002',type:'Bán hàng',qty:-2,note:'HD000'}]"
)
DEMO_NOTICE = (
    "<b>Dữ liệu demo:</b> các số liệu hiển thị chỉ minh họa; "
    "chưa kết nối PostgreSQL, BigQuery hoặc LightGBM."
)
REAL_NOTICE = (
    "<b>Dữ liệu thật từ BigQuery:</b> sản phẩm và tồn kho; dự báo 7/30 ngày cho các mã đã có mô hình (LightGBM/SBA), cập nhật khi chạy lại mô hình. "
    "Hóa đơn và phiếu tạo trên web thực hiện qua trang nhập phiếu."
)


APP_VERSION = "v4.7 (một luồng ghi phiếu: giao diện → API → BigQuery)"


def _client_tu_utils():
    """Tìm đối tượng bigquery.Client có sẵn trong utils.bigquery_client (nếu có)."""
    try:
        from google.cloud import bigquery
        import utils.bigquery_client as m
    except Exception:
        return None
    for ten in dir(m):
        obj = getattr(m, ten, None)
        if isinstance(obj, bigquery.Client):
            return obj
    for ten in ("get_client", "get_bigquery_client", "get_bq_client", "create_client", "client"):
        f = getattr(m, ten, None)
        if callable(f):
            try:
                c = f()
                if isinstance(c, bigquery.Client):
                    return c
            except Exception:
                pass
    return None


def doc_bang(sql: str):
    """Đọc bảng bằng load_data; nếu lỗi thì đọc lại bằng REST (không dùng Storage API)."""
    try:
        return load_data(sql)
    except Exception as e1:
        client = _client_tu_utils()
        if client is None:
            raise
        try:
            return client.query(sql).result().to_dataframe(create_bqstorage_client=False)
        except Exception as e2:
            raise RuntimeError(f"load_data: {type(e1).__name__}: {e1} | REST: {type(e2).__name__}: {e2}") from e2


def _trong(x) -> bool:
    """True nếu giá trị BigQuery là NULL (None, NaN, NaT hoặc pd.NA)."""
    if x is None:
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _so(x, mac_dinh=0.0):
    """Đổi giá trị BigQuery sang số; NULL/NaN/pd.NA thì trả về mac_dinh."""
    if _trong(x):
        return mac_dinh
    try:
        return float(x)
    except (TypeError, ValueError):
        return mac_dinh


def _chu(x) -> str:
    return x if isinstance(x, str) else ""


def _ngay(x) -> str:
    return "" if _trong(x) else str(x)[:10]


@st.cache_data(ttl=60, show_spinner=False)
def load_products(sql: str = SQL) -> list[dict]:
    df = doc_bang(sql)

    products = []
    for r in df.itertuples(index=False):
        co_mo_hinh = bool(r.co_mo_hinh)
        item = (
            {
                "sku": str(r.sku),
                "name": r.ten if isinstance(r.ten, str) and r.ten else str(r.sku),
                "category": r.nhom if isinstance(r.nhom, str) and r.nhom else "Chưa phân loại",
                "price": int(_so(r.gia_ban, _so(r.gia_von))),  # giá bán, thiếu thì dùng giá vốn
                "costPrice": int(_so(r.gia_von)),               # giá vốn (cột "Giá vốn" của trang sản phẩm)
                "stock": int(round(_so(r.ton_live, _so(r.ton_hien_tai)))),  # tồn live, GIỮ NGUYÊN số âm để người dùng thấy
                "min": int(round(_so(r.ton_toi_thieu))),
                "supplier": _chu(r.ma_nha_cung_cap),
                "forecast7": int(round(_so(r.du_bao_7))),
                "forecast30": int(round(_so(r.du_bao_30))),
                "hasModel": co_mo_hinh,
                "sold90": bool(r.co_ban_90_ngay),         # có bán trong 90 ngày cuối của dữ liệu
            }
        )
        anh = _chu(r.anh).strip()
        if anh.lower().startswith("http"):
            item["image"] = anh  # ảnh đầu tiên trong cột image_urls của bảng product
        if co_mo_hinh:  # trường chi tiết chỉ cho món có mô hình (giảm dung lượng trang)
            item.update(
                {
                    "alert": _chu(r.muc_canh_bao),             # DA_HET_HANG, SAP_HET_7_NGAY, ...
                    "reorder": int(_so(r.nen_nhap_goi_y)),       # số lượng nên nhập gợi ý (7 ngày)
                    "reorder30": int(_so(r.nen_nhap_du_30_ngay)),  # số lượng nên nhập đủ 30 ngày
                    "incoming": int(_so(r.hang_dang_ve)),        # hàng đang về
                    "daysLeft": round(_so(r.so_ngay_ton_kho, -1), 1),  # -1 = không có
                    "holiday7": _chu(r.dip_trong_7),
                    "holiday30": _chu(r.dip_trong_30),
                    "tiedCapital": int(_so(r.tien_dong_von)),    # tiền vốn bị kẹt (VNĐ)
                    "max": int(round(_so(r.ton_toi_da))),        # tồn tối đa theo chính sách
                    "top50": bool(r.la_top50) if not _trong(r.la_top50) else False,  # thuộc top 50 SKU
                    "abc": _chu(r.nhom_abcxyz),                  # nhóm ABC-XYZ
                    "excessQty": int(_so(r.so_luong_nen_xa)),    # số lượng nên đẩy đi (tồn quá nhiều)
                    "rev7": int(round(_so(r.doanh_thu_7_co_le))),    # doanh thu dự báo 7 ngày (có tính dịp lễ)
                    "rev30": int(round(_so(r.doanh_thu_30_co_le))),  # doanh thu dự báo 30 ngày (có tính dịp lễ)
                }
            )
        products.append(item)
    return products


@st.cache_data(ttl=600, show_spinner=False)
def load_meta() -> dict:
    df = doc_bang(SQL_META)
    if df.empty:
        return {}
    meta = {
        "forecastFrom": _ngay(df["ngay_du_bao"].iloc[0]),
        "modelSkus": int(_so(df["so_mo_hinh"].iloc[0])),
    }
    # Thông tin mô hình (LightGBM/SBA...) và độ chính xác: thiếu thì bỏ qua, trang vẫn chạy.
    try:
        dm = doc_bang(SQL_MODEL)
        if not dm.empty:
            meta["methods"] = [
                {"name": str(r.cach), "skus": int(_so(r.so_sku))} for r in dm.itertuples(index=False)
            ]
            meta["forecastTo"] = _ngay(dm["den"].max())
            meta["runDate"] = _ngay(dm["ngay_chay"].max())
            meta["lastSale"] = _ngay(dm["ngay_ban_cuoi"].iloc[0])
    except Exception:
        pass
    try:
        ds = doc_bang(SQL_SCORE)
        meta["scores"] = [
            {
                "name": str(r.cach),
                "wapeWeek": _so(r.WAPE_tuan),
                "wape": _so(r.WAPE),
                "r2": _so(r.R2_tuan),
                "mae": _so(r.MAE),
                "runDate": _ngay(r.ngay_chay),
            }
            for r in ds.itertuples(index=False)
        ]
    except Exception:
        pass
    # Chuỗi doanh thu theo ngày (thực tế + dự báo có tính lễ) và dịp lễ cho biểu đồ.
    try:
        da = doc_bang(SQL_SERIES_ACTUAL)
        df_ = doc_bang(SQL_SERIES_FORECAST)
        series = {
            "actual": [[_ngay(r.ngay), int(_so(r.doanh_thu))] for r in da.itertuples(index=False)],
            "forecast": [
                [_ngay(r.ngay), int(_so(r.dt_co_le)), int(_so(r.dt_nen)), _chu(r.dip)]
                for r in df_.itertuples(index=False)
            ],
            "windows": [],
            "holidays": [],
        }
        try:
            dd = doc_bang(SQL_DIP)
            for r in dd.itertuples(index=False):
                series["windows"].append(
                    {
                        "start": _ngay(r.ngay_bat_dau),
                        "days": int(_so(r.so_ngay_anh_huong, 1)),
                        "factor": round(_so(r.he_so, 1.0), 3),
                        "name": _chu(r.ten_dip),
                    }
                )
                series["holidays"].append({"date": _ngay(r.ngay_le), "name": _chu(r.ten_dip)})
        except Exception:
            pass
        meta["series"] = series
    except Exception:
        pass
    try:
        dt = doc_bang(SQL_TOP_LE)
        meta["topHoliday"] = [
            {
                "sku": str(r.sku),
                "dip": _chu(r.ten_dip),
                "base": round(_so(r.du_bao_nen), 2),
                "withHoliday": round(_so(r.du_bao_co_le), 2),
                "factor": round(_so(r.he_so, 1.0), 3),
                "diff": int(round(_so(r.chenh_lech_doanh_thu))),
                "trust": _chu(r.do_tin_cay),
            }
            for r in dt.itertuples(index=False)
        ]
    except Exception:
        pass
    try:
        dtop = doc_bang(SQL_TOP_SOLD)
        top_sold = {"7": [], "30": []}
        for r in dtop.itertuples(index=False):
            top_sold.setdefault(str(int(_so(r.ky))), []).append([str(r.sku), int(round(_so(r.qty)))])
        meta["topSold"] = top_sold
    except Exception:
        pass
    try:
        dn = doc_bang(SQL_DOANH_THU_NGAY)
        meta["doanhThuNgay"] = [[_ngay(r.ngay), round(_so(r.doanh_thu))] for r in dn.itertuples(index=False)]
    except Exception:
        pass
    try:
        dl = doc_bang(SQL_DONG_BO_LUC)
        if not dl.empty and pd.notna(dl["luc"].iloc[0]):
            ts = pd.Timestamp(dl["luc"].iloc[0])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            meta["dongBoLuc"] = ts.isoformat()
    except Exception:
        pass
    try:
        dd = doc_bang(SQL_DOANH_THU_DU_BAO_NGAY)
        meta["doanhThuDuBaoNgay"] = [[_ngay(r.ngay), round(_so(r.doanh_thu))] for r in dd.itertuples(index=False)]
    except Exception:
        pass
    return meta


@st.cache_data(ttl=600, show_spinner=False)
def load_suppliers() -> list[dict]:
    df = doc_bang(SQL_NCC)
    out = []
    for r in df.itertuples(index=False):
        out.append(
            {
                "id": str(r.supplier_id),
                "name": r.supplier_name if isinstance(r.supplier_name, str) and r.supplier_name else str(r.supplier_id),
                "phone": _chu(r.phone),
                "leadTime": None if _trong(r.lead_time_days) else int(r.lead_time_days),
                "count": int(_so(r.so_san_pham)),
                "status": None if _trong(r.status) else int(r.status),
            }
        )
    return out


def _cau_hinh(bien_moi_truong: str, muc: str, khoa: str, mac_dinh: str = "") -> str:
    """Đọc cấu hình: biến môi trường trước, rồi st.secrets[muc][khoa], cuối cùng là giá trị mặc định."""
    v = os.environ.get(bien_moi_truong)
    if v:
        return v.strip()
    try:
        return str(st.secrets[muc][khoa]).strip()
    except Exception:
        return mac_dinh


# Địa chỉ API ghi phiếu (FastAPI). Chạy trên máy: mặc định http://127.0.0.1:8000.
# Triển khai: đặt trong .streamlit/secrets.toml  ->  [api]  base_url = "https://ten-api.run.app"  key = "..."
API_BASE = _cau_hinh(
    "SHELFSENSE_API_BASE",
    "api",
    "base_url",
    "https://smart-inventory-jfy7.onrender.com"
).rstrip("/")
st.write("API đang sử dụng:", API_BASE)
# Khóa API nằm trong trang nên người xem trang đều đọc được: chỉ có tác dụng khi bản thân app Streamlit đã bị giới hạn người dùng.
API_KEY = _cau_hinh("SHELFSENSE_API_KEY", "api", "key", "")


def patch_html(html: str, products: list[dict], meta: dict, suppliers: list[dict] | None = None) -> str:
    data = json.dumps(products, ensure_ascii=False).replace("</", "<\\/")
    html, n = re.subn(
        r"const initialProducts=\[.*?\}\];",
        lambda m: "const initialProducts=" + data + ";",
        html,
        count=1,
        flags=re.S,
    )
    if n != 1 or DEMO_ORDERS not in html:
        raise ValueError("Không tìm thấy dữ liệu mẫu trong shelfsense.html để thay thế")

    html = html.replace(DEMO_ORDERS, "invoices:[],movements:[]")
    html = html.replace(DEMO_NOTICE, REAL_NOTICE)
    html = html.replace("Số liệu demo", "Dữ liệu thực tế từ BigQuery")

    # Báo cho shelfsense.html biết đang chạy với dữ liệu BigQuery (bật các trang dự báo/cảnh báo thật).
    payload = {"meta": meta, "suppliers": suppliers or [], "apiBase": API_BASE, "apiKey": API_KEY}
    script = (
        "<script>window.__SHELFSENSE__="
        + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        + ";</script>"
    )
    html = html.replace("</head>", script + "</head>", 1) if "</head>" in html else script + html
    if suppliers:  # thay nhà cung cấp mẫu bằng dữ liệu BigQuery (chạy sau script của trang)
        html = html.replace("</body>", NCC_SCRIPT + "</body>", 1) if "</body>" in html else html + NCC_SCRIPT


    # Khóa localStorage chỉ phụ thuộc DANH SÁCH MÃ HÀNG, không phụ thuộc tồn/dự báo.
    # (Nếu băm cả dữ liệu thì tồn live đổi là khóa đổi, người dùng mất dữ liệu lưu trong trình duyệt.)
    key = "shelfsense-bq-" + hashlib.md5(
        ",".join(sorted(p["sku"] for p in products)).encode("utf-8")
    ).hexdigest()[:10]
    return html.replace("shelfsense-demo-v1", key)


st.set_page_config(
    page_title="ShelfSense — Smart Inventory",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    #MainMenu, footer, header[data-testid="stHeader"], [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="stStatusWidget"],
    [data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none !important}
    [data-testid="stAppViewContainer"], [data-testid="stMain"] {overflow:hidden !important}
    .block-container {padding:0 !important; max-width:100% !important}
    [data-testid="stVerticalBlock"] {gap:0 !important}
    iframe {height:100vh !important; width:100% !important; border:0 !important; display:block}
    </style>
    """,
    unsafe_allow_html=True,
)

# ?refresh=1 -> xóa bộ nhớ đệm, đọc lại BigQuery
try:
    if st.query_params.get("refresh"):
        st.cache_data.clear()
except Exception:
    pass

html = HTML_FILE.read_text(encoding="utf-8")
loi_doc = None
try:
    try:
        products = load_products(SQL)
    except Exception as exc_chinh:  # view tồn live lỗi -> thử đọc từ ton_hien_tai
        products = load_products(SQL_DU_PHONG)
        st.toast(f"Không đọc được v_ton_live, dùng ton_hien_tai: {type(exc_chinh).__name__}", icon="⚠️")
    try:
        meta = load_meta()
    except Exception:
        meta = {}
    try:
        suppliers = load_suppliers()
    except Exception:
        suppliers = []
    html = patch_html(html, products, meta, suppliers)
    so_sp = f"{len(products):,}".replace(",", ".")
    nhan = (
        '<div style="position:fixed;right:10px;bottom:8px;z-index:99998;background:#ffffffcc;color:#527b45;'
        'padding:3px 10px;border-radius:999px;font:600 11px system-ui,sans-serif;box-shadow:0 1px 4px #0002">'
        f"BigQuery · {so_sp} sản phẩm · app {APP_VERSION}</div>"
    )
    html = html.replace("</body>", nhan + "</body>", 1)

    if meta.get("forecastFrom"):
        tuoi = (date.today() - date.fromisoformat(meta["forecastFrom"])).days
        if tuoi > TUOI_DU_BAO_TOI_DA:
            st.toast(
                f"Dự báo bắt đầu từ {meta['forecastFrom']} (cách {tuoi} ngày). "
                "Cần đồng bộ phiếu và chạy lại mô hình.",
                icon="⚠️",
            )
except Exception as exc:  # lỗi xác thực, mạng, quyền hoặc schema BigQuery
    loi_doc = f"{type(exc).__name__}: {exc}\\n" + "".join(traceback.format_exc().splitlines(True)[-8:])

if loi_doc:
    st.error("Không thể tải dữ liệu thật từ BigQuery. ShelfSense đã dừng để tránh hiển thị dữ liệu demo.")
    st.code(loi_doc)
    st.info("Kiểm tra quyền BigQuery, Google credentials và tên bảng/view trong SQL.")
    st.stop()

if hasattr(st, "iframe"):
    st.iframe(html, height=900)
else:
    import streamlit.components.v1 as components

    components.html(html, height=900, scrolling=True)
