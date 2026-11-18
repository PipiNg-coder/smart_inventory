-- =====================================================================
-- 05_van_hanh_v2.sql - Tạo dataset van_hanh_v2, bảng ghi phiếu, view tồn live, sao lưu
-- Chạy TỪNG KHỐI (bôi đen khối rồi Run).
-- Quy ước dấu số lượng (so_luong_thay_doi): NHAP +, BAN -, HUY -, TRA_HANG +
--   (TRA_HANG = khách trả hàng, hàng quay về kho)
-- Bảng ở sandbox tự hết hạn sau 60 ngày.
-- =====================================================================


-- =====================================================================
-- KHỐI 1. Tạo dataset (cùng vị trí asia-southeast1 với mart_data_v2)
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS `he-thong-ton-kho-ban-le.van_hanh_v2`
OPTIONS (location = 'asia-southeast1');


-- =====================================================================
-- KHỐI 2. SAO LƯU 4 bảng trong mart_data_v2 sang van_hanh_v2
-- Làm TRƯỚC khi chạy đồng bộ lần đầu. Muốn khôi phục xem cuối file.
-- =====================================================================
CREATE OR REPLACE TABLE van_hanh_v2.sao_luu_fact_sales     AS SELECT * FROM mart_data_v2.fact_sales;
CREATE OR REPLACE TABLE van_hanh_v2.sao_luu_fact_inventory AS SELECT * FROM mart_data_v2.fact_inventory;
CREATE OR REPLACE TABLE van_hanh_v2.sao_luu_ton_hien_tai   AS SELECT * FROM mart_data_v2.ton_hien_tai;
CREATE OR REPLACE TABLE van_hanh_v2.sao_luu_stock_movement AS SELECT * FROM mart_data_v2.stock_movement;


-- =====================================================================
-- KHỐI 3. 5 bảng chỉ-thêm-dòng
-- =====================================================================

-- 3.1 Thông tin chung của phiếu. Mỗi lần tạo / sửa / hủy là thêm 1 dòng (phien_ban tăng dần)
CREATE TABLE IF NOT EXISTS van_hanh_v2.phieu_log (
  ma_phieu        STRING,
  phien_ban       INT64,
  loai_phieu      STRING,      -- NHAP | BAN | HUY | TRA_HANG
  ngay_phieu      DATE,
  doi_tac         STRING,
  hanh_dong       STRING,      -- TAO | SUA | HUY_PHIEU
  trang_thai      STRING,      -- HIEU_LUC | DA_HUY
  ly_do           STRING,
  nguoi_thuc_hien STRING,
  thoi_diem       TIMESTAMP,
  la_phieu_thu    BOOL         -- TRUE: phiếu thử khi demo, KHÔNG được đồng bộ vào mart_data_v2
);

-- 3.2 Các món trong phiếu, ghi đủ toàn bộ dòng của mỗi phiên bản
CREATE TABLE IF NOT EXISTS van_hanh_v2.phieu_chi_tiet_log (
  ma_phieu   STRING,
  phien_ban  INT64,
  sku        STRING,
  so_luong   FLOAT64,
  don_gia    FLOAT64
);

-- 3.3 Biến động kho. Sửa phiếu = ghi phần chênh lệch, hủy phiếu = ghi số ngược dấu
CREATE TABLE IF NOT EXISTS van_hanh_v2.stock_movement_live (
  movement_id       STRING,    -- = ma_phieu_vPHIENBAN_sku, không trùng
  thoi_diem         TIMESTAMP,
  ngay              DATE,
  ma_phieu          STRING,
  phien_ban         INT64,
  sku               STRING,
  loai_giao_dich    STRING,    -- NHAP | BAN | HUY | TRA_HANG
  so_luong_thay_doi FLOAT64,   -- có dấu
  don_gia           FLOAT64,
  la_phieu_thu      BOOL
);

-- 3.4 Nhật ký đồng bộ: dòng biến động nào đã được chép sang mart_data_v2 (chống chép trùng)
CREATE TABLE IF NOT EXISTS van_hanh_v2.nhat_ky_dong_bo (
  movement_id  STRING,
  dong_bo_luc  TIMESTAMP,
  dot_chay     STRING,
  trang_thai   STRING          -- DA_DONG_BO | BO_QUA_PHIEU_THU | BO_QUA_SKU_LA
);

-- 3.5 Nhật ký chạy (web dùng để hiện "Dữ liệu cập nhật đến ...")
CREATE TABLE IF NOT EXISTS van_hanh_v2.lan_chay_mo_hinh (
  thoi_diem_chay     TIMESTAMP,
  loai               STRING,   -- DONG_BO | MO_HINH
  ngay_du_lieu_cuoi  DATE,
  trang_thai         STRING,
  ghi_chu            STRING
);


-- =====================================================================
-- KHỐI 4. View
-- =====================================================================

-- 4.1 Phiếu hiện tại = phiên bản mới nhất của mỗi mã phiếu
CREATE OR REPLACE VIEW van_hanh_v2.v_phieu_hien_tai AS
SELECT * EXCEPT (rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY ma_phieu ORDER BY phien_ban DESC) AS rn
  FROM van_hanh_v2.phieu_log
) WHERE rn = 1;

-- 4.2 Chi tiết món của phiên bản mới nhất
CREATE OR REPLACE VIEW van_hanh_v2.v_chi_tiet_hien_tai AS
SELECT c.*
FROM van_hanh_v2.phieu_chi_tiet_log c
JOIN van_hanh_v2.v_phieu_hien_tai p
  ON p.ma_phieu = c.ma_phieu AND p.phien_ban = c.phien_ban;

-- 4.3 Tồn kho LIVE = tồn đã đồng bộ (mart_data_v2.ton_hien_tai) + biến động CHƯA đồng bộ
-- Biến động đã vào nhat_ky_dong_bo (kể cả phiếu thử bị bỏ qua) thì không tính lại nữa
CREATE OR REPLACE VIEW van_hanh_v2.v_ton_live AS
WITH chua AS (
  SELECT m.sku,
         SUM(m.so_luong_thay_doi) AS bien_dong,
         SUM(IF(m.loai_giao_dich = 'NHAP', m.so_luong_thay_doi, 0)) AS nhap_chua_dong_bo
  FROM van_hanh_v2.stock_movement_live m
  LEFT JOIN van_hanh_v2.nhat_ky_dong_bo n USING (movement_id)
  WHERE n.movement_id IS NULL
  GROUP BY m.sku
)
SELECT h.sku,
       h.ton_cuoi_ngay                                         AS ton_da_dong_bo,
       IFNULL(c.bien_dong, 0)                                  AS bien_dong_chua_dong_bo,
       h.ton_cuoi_ngay + IFNULL(c.bien_dong, 0)                AS ton_live,
       GREATEST(0, h.hang_dang_ve - IFNULL(c.nhap_chua_dong_bo, 0)) AS hang_dang_ve_live
FROM mart_data_v2.ton_hien_tai h
LEFT JOIN chua c USING (sku);


-- =====================================================================
-- KHỐI 5. KIỂM TRA (chạy sau khối 1-4)
-- =====================================================================
-- 5.1 Dataset cùng vị trí: hai view phải chạy được
SELECT COUNT(*) AS so_sku FROM van_hanh_v2.v_ton_live;

-- 5.2 Các bảng sao lưu có đủ dòng không (so với bảng gốc trong mart_data_v2)
SELECT 'fact_sales' AS bang,
       (SELECT COUNT(*) FROM mart_data_v2.fact_sales) AS goc,
       (SELECT COUNT(*) FROM van_hanh_v2.sao_luu_fact_sales) AS sao_luu
UNION ALL SELECT 'fact_inventory',
       (SELECT COUNT(*) FROM mart_data_v2.fact_inventory), (SELECT COUNT(*) FROM van_hanh_v2.sao_luu_fact_inventory)
UNION ALL SELECT 'ton_hien_tai',
       (SELECT COUNT(*) FROM mart_data_v2.ton_hien_tai), (SELECT COUNT(*) FROM van_hanh_v2.sao_luu_ton_hien_tai)
UNION ALL SELECT 'stock_movement',
       (SELECT COUNT(*) FROM mart_data_v2.stock_movement), (SELECT COUNT(*) FROM van_hanh_v2.sao_luu_stock_movement);

-- 5.3 Kiểm tra dấu của HUY trong stock_movement CŨ của nhóm
SELECT loai_giao_dich, ly_do_dieu_chinh, COUNT(*) AS n, MIN(so_luong_thay_doi) AS min_sl, MAX(so_luong_thay_doi) AS max_sl
FROM mart_data_v2.stock_movement
WHERE loai_giao_dich IN ('HUY', 'TRA_HANG')
GROUP BY loai_giao_dich, ly_do_dieu_chinh
ORDER BY n DESC
LIMIT 20;


-- =====================================================================
-- KHÔI PHỤC (chỉ dùng khi đồng bộ làm sai, chạy từng câu)
-- =====================================================================
-- CREATE OR REPLACE TABLE mart_data_v2.fact_sales     AS SELECT * FROM van_hanh_v2.sao_luu_fact_sales;
-- CREATE OR REPLACE TABLE mart_data_v2.fact_inventory AS SELECT * FROM van_hanh_v2.sao_luu_fact_inventory;
-- CREATE OR REPLACE TABLE mart_data_v2.ton_hien_tai   AS SELECT * FROM van_hanh_v2.sao_luu_ton_hien_tai;
-- CREATE OR REPLACE TABLE mart_data_v2.stock_movement AS SELECT * FROM van_hanh_v2.sao_luu_stock_movement;
-- Sau khôi phục: xóa nhật ký đồng bộ để lần sau chép lại từ đầu
-- CREATE OR REPLACE TABLE van_hanh_v2.nhat_ky_dong_bo (movement_id STRING, dong_bo_luc TIMESTAMP, dot_chay STRING, trang_thai STRING);
