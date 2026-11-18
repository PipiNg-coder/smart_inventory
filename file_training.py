!pip install -q --upgrade google-cloud-bigquery db-dtypes pandas pyarrow lightgbm scikit-learn joblib matplotlib
# Prophet chỉ cần cho P19 (nếu không muốn chạy thì bỏ qua dòng dưới và đặt CHAY_PROPHET = False ở ô tiếp theo)
!pip install -q prophet
# P20b (ngày lễ Âm lịch) cần thêm thư viện đổi lịch:
!pip install -q lunardate


import warnings, logging
import numpy as np
import pandas as pd
import joblib
import matplotlib.pyplot as plt
from google.cloud import bigquery

try:
    from google.colab import auth
    auth.authenticate_user()
    print('Đã xác thực tài khoản Google.')
except ImportError:
    print('Không chạy trên Colab: dùng thông tin đăng nhập sẵn có của máy.')

# ---------- THÔNG SỐ (chỉ cần sửa ở đây) ----------
PROJECT_ID     = 'he-thong-ton-kho-ban-le'
DATASET        = 'mart_data_v2'             # Dataset mới của nhóm
DATASET_CU     = 'mart_data'                # Dataset cũ dùng để đối chiếu ở Ô 11
HAU_TO_BANG    = '_v2'                      # Giữ nguyên hậu tố '_v2' để tên bảng trùng khớp 100% với bên mart_data cũ
BANG_SKU       = f'sku_chon{HAU_TO_BANG}'   # Bảng danh sách món: sku_chon_v2
HORIZON        = 14                         # Dự đoán tối đa 14 ngày tới. Phải là bội số của 7 (7, 14, 21...)
BO_LA_LE_KHOI_MO_HINH = True                # True: LightGBM không dùng cột la_le, hiệu ứng lễ do P20b xử lý (tránh tính 2 lần)
CHAY_PROPHET   = True                       # False nếu máy không cài được prophet
SO_SKU_PROPHET = 8                          # P19: chạy Prophet cho 8 món doanh thu cao nhất
K_LAN_QUAN_SAT = 1.0                        # P20b: dịp chỉ thấy 1 lần thì chỉ tin hệ số k/(1+k)=50% (0 = tin hoàn toàn)
DIP_TUONG_TU   = {                          # P20b: dịp CHƯA có lịch sử mượn hệ số dịp tương tự
    'nhà giáo':   'phụ nữ việt nam',   # 20/11 ~ 20/10: cùng kiểu tặng quà cho một nhóm người, cách nhau 1 tháng
    'giáng sinh': 'tình nhân',         # Noel ~ Valentine: tặng quà, mua sắm theo cặp/tiệc
    'tết dương':  'quốc khánh',        # 1/1 ~ 2/9: kỳ nghỉ lễ dương lịch
    'halloween':  'thiếu nhi',         # Halloween ~ 1/6: hàng cho trẻ em, lễ hội
}                                           # Đây là GIẢ ĐỊNH của nhóm, ghi rõ trong báo cáo; các dịp này có nhãn MUON_DIP_TUONG_TU
SO_NGAY_CU_CANH_BAO = 3                     # Dữ liệu bán cuối cùng cũ hơn hôm nay quá số ngày này thì in cảnh báo
TIEU_CHI_CHON  = 'WAPE_tuan'                # Tiêu chí chọn cách dự đoán thắng ở P20 (nhỏ hơn là tốt hơn)

warnings.filterwarnings('ignore')
logging.getLogger('cmdstanpy').setLevel(logging.WARNING)
client = bigquery.Client(project=PROJECT_ID)
print(f'Kết nối BigQuery thành công: {PROJECT_ID}.{DATASET} | Hậu tố bảng: "{HAU_TO_BANG}" | Bảng danh sách món: {BANG_SKU}')


def tai_du_lieu_goc(client):
    """Lấy 3 thứ từ BigQuery (mart_data_v2) cho các món trong bảng danh sách món (sku_chon_v2):
    - raw       : số bán từng ngày, ĐỦ CẢ NGÀY BÁN 0. Dựng từ fact_sales (số bán), fact_inventory (hết hàng),
                  và lấy thêm gia_ban từ dim_product dự phòng cho ngày không bán.
    - doanh_thu : tổng doanh thu mỗi món (kết hợp từ fact_sales và sku_chon_v2) -> dùng cho ABC
    - bang_le   : bảng rỗng (ngày lễ do P20b lấy từ bang_le_chuan)
    """
    t = f'`{PROJECT_ID}.{DATASET}`'
    sql_raw = f'''
        WITH khung AS (          -- mỗi món x mỗi ngày từ ngày bán đầu tiên đến ngày bán cuối cùng
          SELECT n AS ngay, CAST(c.sku AS STRING) AS sku
          FROM UNNEST(GENERATE_DATE_ARRAY((SELECT MIN(ngay) FROM {t}.fact_sales),
                                          (SELECT MAX(ngay) FROM {t}.fact_sales))) AS n
          CROSS JOIN {t}.{BANG_SKU} c
        ), ban AS (
          SELECT CAST(sku AS STRING) AS sku, ngay,
                 SUM(CAST(so_luong_ban AS FLOAT64)) AS so_luong_ban,
                 SAFE_DIVIDE(SUM(CAST(so_luong_ban AS FLOAT64) * CAST(gia_tb AS FLOAT64)),
                             NULLIF(SUM(CAST(so_luong_ban AS FLOAT64)), 0)) AS gia_ban,
                 MAX(CAST(co_khuyen_mai AS INT64)) AS co_khuyen_mai
          FROM {t}.fact_sales GROUP BY sku, ngay
        ), ton AS (
          SELECT CAST(sku AS STRING) AS sku, ngay,
                 MAX(CAST(het_hang AS INT64)) AS het_hang
          FROM {t}.fact_inventory GROUP BY sku, ngay
        ), dp AS (
          SELECT CAST(sku AS STRING) AS sku,
                 MAX(CAST(gia_ban AS FLOAT64)) AS gia_ban_dm
          FROM {t}.dim_product GROUP BY sku
        )
        SELECT k.ngay, k.sku,
               IFNULL(b.so_luong_ban, 0)  AS so_luong_ban,      -- ngày không có dòng bán = bán 0
               IFNULL(t.het_hang, 0)      AS het_hang,
               IFNULL(b.co_khuyen_mai, 0) AS co_khuyen_mai,
               0                          AS la_le,             -- hiệu ứng lễ do P20b xử lý
               COALESCE(b.gia_ban, dp.gia_ban_dm) AS gia_ban
        FROM khung k
        LEFT JOIN ban b ON b.sku = k.sku AND b.ngay = k.ngay
        LEFT JOIN ton t ON t.sku = k.sku AND t.ngay = k.ngay
        LEFT JOIN dp ON dp.sku = k.sku'''
    sql_doanh_thu = f'''
        SELECT CAST(s.sku AS STRING) AS sku,
               SUM(CAST(s.so_luong_ban AS FLOAT64) * CAST(s.gia_tb AS FLOAT64)) AS doanh_thu
        FROM {t}.fact_sales s
        JOIN {t}.{BANG_SKU} c ON CAST(s.sku AS STRING) = CAST(c.sku AS STRING)
        GROUP BY s.sku'''
    raw = client.query(sql_raw).to_dataframe()
    doanh_thu = client.query(sql_doanh_thu).to_dataframe()
    bang_le = pd.DataFrame({'ngay': pd.to_datetime([]), 'la_le': pd.Series([], dtype=int)})
    return raw, doanh_thu, bang_le

raw, doanh_thu, bang_le = tai_du_lieu_goc(client)
print('raw      :', raw.shape, '| số món:', raw['sku'].nunique(), '| từ', raw['ngay'].min(), 'đến', raw['ngay'].max())
print('tổng số bán trong raw:', round(float(raw['so_luong_ban'].sum())), '(khớp tổng số bán của fact_sales)')
print('doanh_thu:', doanh_thu.shape)
print('bang_le  :', bang_le.shape)

# 50 món đứng đầu (đọc từ cột la_top50 của bảng sku_chon_v2)
try:
    top50_sku = set(client.query(f'SELECT CAST(sku AS STRING) AS sku FROM `{PROJECT_ID}.{DATASET}.{BANG_SKU}` WHERE la_top50').to_dataframe()['sku'])
except Exception:
    top50_sku = set(doanh_thu.nlargest(50, 'doanh_thu')['sku'])
    print(f'Bảng {BANG_SKU} chưa có cột la_top50: tạm lấy 50 món doanh thu cao nhất.')
print('Số món top 50:', len(top50_sku), '| tổng số món:', raw['sku'].nunique())


# ---------- P15: chuẩn bị bảng ngày-đầy-đủ + chia 3 phần theo thời gian ----------
def lam_sach_bang_ngay(raw):
    """Đề phòng dòng trùng (sku, ngay); điền giá cho ngày không bán; kiểu dữ liệu chuẩn."""
    d = raw.copy()
    d['ngay'] = pd.to_datetime(d['ngay'])
    d = (d.sort_values(['sku', 'ngay'])
           .groupby(['sku', 'ngay'], as_index=False)
           .agg(so_luong_ban=('so_luong_ban', 'max'),
                het_hang=('het_hang', 'max'),
                co_khuyen_mai=('co_khuyen_mai', 'max'),
                la_le=('la_le', 'max'),
                gia_ban=('gia_ban', 'median')))
    for c in ['het_hang', 'co_khuyen_mai', 'la_le']:      # ô trống coi như 0 (không hết hàng / không khuyến mãi / không lễ)
        d[c] = d[c].fillna(0).astype(int)
    d['so_luong_ban'] = d['so_luong_ban'].astype(float)
    d['gia_ban'] = d['gia_ban'].astype(float)
    # ngày không bán thì chưa có giá: lấy giá của lần bán gần nhất (rồi tới lần bán kế tiếp, rồi tới giá trung vị của món)
    d = d.sort_values(['sku', 'ngay'])
    d['gia_ban'] = d.groupby('sku')['gia_ban'].transform(lambda x: x.ffill().bfill())
    return d

def tao_dac_trung(d, horizon=HORIZON):
    """Tự tính lại lag/trung bình trượt, chỉ nhìn dữ liệu cũ hơn `horizon` ngày."""
    d = d.sort_values(['sku', 'ngay']).copy()
    d['thu'] = d['ngay'].dt.dayofweek
    # "hết hàng mà không bán được": kho trống và bán 0 -> không phải khách không mua, nên coi như "không biết".
    # (Ngày kho về 0 cuối ngày nhưng VẪN có bán thì giữ lại, vì đó là ngày bán thật.)
    khong_ban_duoc = (d['het_hang'] == 1) & (d['so_luong_ban'] == 0)
    d['ban_hieu_luc'] = d['so_luong_ban'].where(~khong_ban_duoc)
    g = d.groupby('sku')['ban_hieu_luc']
    d['lag_h']   = g.shift(horizon)
    d['lag_h7']  = g.shift(horizon + 7)
    d['lag_h14'] = g.shift(horizon + 14)
    d['tb_7']  = g.transform(lambda x: x.shift(horizon).rolling(7,  min_periods=3).mean())
    d['tb_28'] = g.transform(lambda x: x.shift(horizon).rolling(28, min_periods=7).mean())
    co_ban = d['ban_hieu_luc'].gt(0).astype(float).where(d['ban_hieu_luc'].notna())
    d['ti_le_co_ban_28'] = co_ban.groupby(d['sku']).transform(
        lambda x: x.shift(horizon).rolling(28, min_periods=7).mean())
    return d

def chia_3_phan(d, ti_le_hoc=0.70, ti_le_chinh=0.15):
    """Chia theo THỨ TỰ NGÀY: 70% ngày cũ = học, 15% giữa = chỉnh, 15% mới nhất = thi."""
    ngay = np.sort(d['ngay'].unique())
    n = len(ngay)
    het_hoc   = ngay[int(n * ti_le_hoc) - 1]
    het_chinh = ngay[int(n * (ti_le_hoc + ti_le_chinh)) - 1]
    hoc   = d[d['ngay'] <= het_hoc]
    chinh = d[(d['ngay'] > het_hoc) & (d['ngay'] <= het_chinh)]
    thi   = d[d['ngay'] > het_chinh]
    return hoc, chinh, thi, (het_hoc, het_chinh)

# ---------- GỌI HÀM P15 ----------
so_dong_truoc = len(raw)
d = lam_sach_bang_ngay(raw)                       # 1) bỏ dòng trùng
print(f'Bỏ dòng trùng: {so_dong_truoc:,} -> {len(d):,} dòng')
assert d.duplicated(['sku', 'ngay']).sum() == 0

d = tao_dac_trung(d, HORIZON)                     # 2) tạo cột trí nhớ

hoc, chinh, thi, (het_hoc, het_chinh) = chia_3_phan(d)   # 3) chia theo ngày
print('Học  :', hoc['ngay'].min().date(), '->', hoc['ngay'].max().date(), f'({len(hoc):,} dòng)')
print('Chỉnh:', chinh['ngay'].min().date(), '->', chinh['ngay'].max().date(), f'({len(chinh):,} dòng)')
print('Thi  :', thi['ngay'].min().date(), '->', thi['ngay'].max().date(), f'({len(thi):,} dòng)')

# Chỉ giữ dòng dùng được: không phải 'kho trống không bán được' + đã đủ lịch sử để tính cột trí nhớ
def dung_duoc(x):
    kho_trong_khong_ban = (x['het_hang'] == 1) & (x['so_luong_ban'] == 0)    # kho trống và không bán được gì
    return x[~kho_trong_khong_ban & x['lag_h'].notna() & x['tb_28'].notna()]
hoc_d, chinh_d, thi_d = dung_duoc(hoc), dung_duoc(chinh), dung_duoc(thi)
print(f'Dòng dùng được -> học {len(hoc_d):,} | chỉnh {len(chinh_d):,} | thi {len(thi_d):,}')

# ---------- P16: ABC-XYZ (Tối ưu hóa tốc độ bằng Vectorization) ----------
def phan_loai_abc_xyz(doanh_thu, d_ngay, nguong_a=0.80, nguong_b=0.95, nguong_x=0.5, nguong_y=1.0):
    """doanh_thu: bảng (sku, doanh_thu). d_ngay: bảng ngày-đầy-đủ (có so_luong_ban)."""
    a = doanh_thu.sort_values('doanh_thu', ascending=False).reset_index(drop=True)
    tong = a['doanh_thu'].sum()
    a['ty_le_luy_ke'] = a['doanh_thu'].cumsum() / max(tong, 1e-9)
    truoc = a['ty_le_luy_ke'] - a['doanh_thu'] / max(tong, 1e-9)      # tỷ lệ lũy kế TRƯỚC khi cộng món này
    a['nhom_abc'] = np.where(truoc < nguong_a, 'A', np.where(truoc < nguong_b, 'B', 'C'))

    # Lọc từ ngày bán đầu tiên của từng SKU để tính biến động tuần chuẩn xác và nhanh
    co_ban_min = d_ngay.loc[d_ngay['so_luong_ban'] > 0].groupby('sku')['ngay'].min()
    d_hop_le = d_ngay.merge(co_ban_min.rename('ngay_bd'), on='sku', how='inner')
    d_hop_le = d_hop_le[d_hop_le['ngay'] >= d_hop_le['ngay_bd']].copy()

    if not d_hop_le.empty:
        d_hop_le['tuan'] = d_hop_le['ngay'].dt.to_period('W-SUN')
        tuan_df = d_hop_le.groupby(['sku', 'tuan'], as_index=False)['so_luong_ban'].sum().sort_values(['sku', 'tuan'])
        # Bỏ tuần cuối nếu SKU có nhiều hơn 1 tuần (thường tuần cuối chưa đủ 7 ngày)
        so_tuan = tuan_df.groupby('sku')['tuan'].transform('count')
        la_tuan_cuoi = tuan_df.groupby('sku').cumcount(ascending=False) == 0
        tuan_df = tuan_df[~(la_tuan_cuoi & (so_tuan > 1))]

        thong_ke_tuan = tuan_df.groupby('sku')['so_luong_ban'].agg(
            tb='mean',
            lech=lambda x: np.std(x, ddof=0)
        )
        cv_series = np.where(thong_ke_tuan['tb'] > 0, thong_ke_tuan['lech'] / thong_ke_tuan['tb'], np.inf)
        cv_map = pd.Series(cv_series, index=thong_ke_tuan.index).to_dict()
    else:
        cv_map = {}

    a['cv_tuan'] = a['sku'].map(cv_map).fillna(np.inf)
    a['nhom_xyz'] = np.where(a['cv_tuan'] < nguong_x, 'X', np.where(a['cv_tuan'] <= nguong_y, 'Y', 'Z'))
    a['nhom_abcxyz'] = a['nhom_abc'] + a['nhom_xyz']
    return a

def luu_bigquery(df, ten_bang, schema=None, hau_to=HAU_TO_BANG):
    """Ghi bảng lên BigQuery với hậu tố tên bảng mới (không đè lên bảng gốc của nhóm)."""
    ten_bang_dich = f'{ten_bang}{hau_to}'
    cau_hinh = bigquery.LoadJobConfig(write_disposition='WRITE_TRUNCATE', schema=schema)
    client.load_table_from_dataframe(df, f'{PROJECT_ID}.{DATASET}.{ten_bang_dich}', job_config=cau_hinh).result()
    print(f'Đã ghi {len(df):,} dòng vào {DATASET}.{ten_bang_dich} (Bảng gốc {DATASET}.{ten_bang} được giữ nguyên)')

# ---------- GỌI HÀM P16 ----------
nhom = phan_loai_abc_xyz(doanh_thu, d, nguong_a=0.80, nguong_b=0.95, nguong_x=0.5, nguong_y=1.0)
nhom['cv_tuan'] = nhom['cv_tuan'].replace(np.inf, np.nan)    # món chưa bán lần nào: để trống, xếp nhóm Z
print(pd.crosstab(nhom['nhom_abc'], nhom['nhom_xyz'], margins=True))
luu_bigquery(nhom, 'nhom_abcxyz')

# ---------- P17: Baseline ----------
def du_doan_baseline(d_chung_dac_trung):
    """Hai cách đơn giản nhất. Cùng mốc thông tin với LightGBM (dữ liệu cũ hơn HORIZON ngày)."""
    out = d_chung_dac_trung.copy()
    out['du_doan_naive'] = out['lag_h']     # cùng thứ của tuần trước (vì HORIZON là bội số của 7)
    out['du_doan_tb7']   = out['tb_7']      # trung bình trượt 7 ngày
    return out

# ---------- Thước đo đánh giá ----------
def tinh_diem(y_that, y_doan):
    y_that = np.asarray(y_that, float); y_doan = np.asarray(y_doan, float)
    ok = ~np.isnan(y_doan) & ~np.isnan(y_that)
    y_that, y_doan = y_that[ok], np.clip(y_doan[ok], 0.0, None)
    mae = np.mean(np.abs(y_that - y_doan))
    rmse = np.sqrt(np.mean((y_that - y_doan) ** 2))
    wape = np.abs(y_that - y_doan).sum() / max(y_that.sum(), 1e-9)
    bias = (y_doan.sum() - y_that.sum()) / max(y_that.sum(), 1e-9)
    mau_smape = np.abs(y_that) + np.abs(y_doan)
    co_so = mau_smape > 0
    smape = np.mean(2.0 * np.abs(y_that[co_so] - y_doan[co_so]) / mau_smape[co_so]) if co_so.any() else 0.0
    return {'MAE': mae, 'RMSE': rmse, 'WAPE': wape, 'SMAPE': smape, 'Lech_tong': bias, 'So_dong': int(ok.sum())}

def tinh_diem_tuan(df, cot):
    """WAPE và R2 khi cộng số bán và số dự đoán theo từng khối 7 ngày của mỗi món."""
    t = df.dropna(subset=[cot]).copy()
    t['khoi'] = (t['ngay'] - t['ngay'].min()).dt.days // 7
    g = t.groupby(['sku', 'khoi']).agg(that=('so_luong_ban', 'sum'), doan=(cot, 'sum'))
    wape_tuan = np.abs(g['that'] - g['doan']).sum() / max(g['that'].sum(), 1e-9)
    ss_res = np.sum((g['that'] - g['doan']) ** 2)
    ss_tot = np.sum((g['that'] - g['that'].mean()) ** 2)
    r2_tuan = 1.0 - (ss_res / max(ss_tot, 1e-9)) if ss_tot > 0 else np.nan
    return wape_tuan, r2_tuan

def bang_diem_cho(df, cac_cot):
    """Chấm điểm nhiều cách dự đoán trên CÙNG một tập dòng để so sánh công bằng."""
    hang = []
    for cot in cac_cot:
        diem = tinh_diem(df['so_luong_ban'], df[cot])
        wape_t, r2_t = tinh_diem_tuan(df, cot)
        diem['WAPE_tuan'] = wape_t
        diem['R2_tuan'] = r2_t
        diem['Cach'] = cot
        hang.append(diem)
    kq = pd.DataFrame(hang)[['Cach', 'MAE', 'RMSE', 'WAPE', 'WAPE_tuan', 'SMAPE', 'R2_tuan', 'Lech_tong', 'So_dong']]
    return kq.sort_values(TIEU_CHI_CHON).reset_index(drop=True)

# ---------- GỌI HÀM P17 ----------
d = du_doan_baseline(d)
thi_d = du_doan_baseline(thi_d)
diem_baseline = bang_diem_cho(thi_d.dropna(subset=['du_doan_naive', 'du_doan_tb7']), ['du_doan_naive', 'du_doan_tb7'])
print('=== ĐIỂM BASELINE TRÊN PHẦN THI ===')
print(diem_baseline.round(3).to_string(index=False))


import lightgbm as lgb

CAC_COT_SO = ['lag_h', 'lag_h7', 'lag_h14', 'tb_7', 'tb_28', 'ti_le_co_ban_28',
              'thu', 'la_le', 'co_khuyen_mai']
if BO_LA_LE_KHOI_MO_HINH:
    CAC_COT_SO.remove('la_le')

def tinh_thong_tin_sku(df_hoc, tat_ca_sku):
    """Những con số 'thuộc về từng món', chỉ tính từ dữ liệu học để không rò rỉ."""
    gia_chuan = df_hoc.groupby('sku')['gia_ban'].median().replace(0, np.nan)
    return {'ban_tb_sku': df_hoc.groupby('sku')['ban_hieu_luc'].mean(),
            'gia_chuan': gia_chuan,
            'danh_muc_sku': sorted(tat_ca_sku)}

def tao_ma_tran_x(df, thong_tin):
    """Biến bảng dòng-ngày thành bảng cột đặc trưng đưa vào LightGBM."""
    X = pd.DataFrame(index=df.index)
    for c in CAC_COT_SO:
        X[c] = df[c]
    X['gia_tuong_doi'] = df['gia_ban'] / df['sku'].map(thong_tin['gia_chuan'])   # 1.0 = giá thường, <1 = đang giảm giá
    X['ban_tb_sku'] = df['sku'].map(thong_tin['ban_tb_sku'])                      # món bán nhiều hay ít
    X['sku_ma'] = pd.Categorical(df['sku'], categories=thong_tin['danh_muc_sku'])  # LightGBM tự hiểu đây là nhãn món
    return X

def huan_luyen_lightgbm(hoc_d, chinh_d, thong_tin):
    """Học trên phần học, dừng sớm theo phần chỉnh."""
    mo_hinh = lgb.LGBMRegressor(
        objective='poisson', n_estimators=2000, learning_rate=0.03,
        num_leaves=15, min_child_samples=20, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=1.0, random_state=42, verbose=-1)
    mo_hinh.fit(tao_ma_tran_x(hoc_d, thong_tin), hoc_d['so_luong_ban'],
                eval_set=[(tao_ma_tran_x(chinh_d, thong_tin), chinh_d['so_luong_ban'])],
                callbacks=[lgb.early_stopping(stopping_rounds=100, verbose=False)])
    return mo_hinh

def do_quan_trong(mo_hinh, ten_cot):
    """Cột nào được mô hình dùng nhiều nhất (tính theo mức giảm sai số)."""
    kq = pd.DataFrame({'cot': ten_cot, 'quan_trong': mo_hinh.booster_.feature_importance(importance_type='gain')})
    kq['ty_le'] = kq['quan_trong'] / kq['quan_trong'].sum()
    return kq.sort_values('quan_trong', ascending=False).reset_index(drop=True)

# ---------- GỌI HÀM P18 ----------
thong_tin = tinh_thong_tin_sku(hoc_d, d['sku'].unique())          # 1) số riêng từng món (từ phần học)
mo_hinh = huan_luyen_lightgbm(hoc_d, chinh_d, thong_tin)           # 2) huấn luyện
print('Số cây tối ưu (dừng sớm):', mo_hinh.best_iteration_)

X_thi = tao_ma_tran_x(thi_d, thong_tin)                            # 3) dự đoán phần thi
thi_d['du_doan_lgbm'] = mo_hinh.predict(X_thi)

print('\n=== ĐỘ QUAN TRỌNG CỦA CÁC CỘT ===')
print(do_quan_trong(mo_hinh, list(X_thi.columns)).round(3).to_string(index=False))

# ---------- P19: Croston / SBA cho hàng bán ngắt quãng ----------
def croston_sba(ban_hieu_luc, alpha=0.1, sba=True):
    """Trả về dãy dự báo f[t] = dự báo số bán/ngày, tính từ dữ liệu đến hết ngày t."""
    x = np.asarray(ban_hieu_luc, float)
    f = np.full(len(x), np.nan)
    z = None; p = None; q = 1
    for t, v in enumerate(x):
        if np.isnan(v):                     # ngày hết hàng: bỏ qua, không cập nhật
            f[t] = (z / p) * (1 - alpha / 2 if sba else 1) if z is not None else np.nan
            continue
        if v > 0:
            if z is None: z, p = v, q
            else:
                z = z + alpha * (v - z)
                p = p + alpha * (q - p)
            q = 1
        else:
            q += 1
        f[t] = (z / p) * (1 - alpha / 2 if sba else 1) if z is not None else np.nan
    return f

def them_croston(d, horizon=HORIZON):
    d = d.sort_values(['sku', 'ngay']).copy()
    d['du_doan_sba'] = d.groupby('sku')['ban_hieu_luc'].transform(
        lambda s: pd.Series(croston_sba(s.values), index=s.index).shift(horizon))
    return d

def chay_prophet(d, danh_sach_sku, het_chinh, ngay_thi):
    """Học trên (học + chỉnh), đoán cho các ngày thi. Trả về bảng (sku, ngay, du_doan_prophet)."""
    try:
        from prophet import Prophet
    except ImportError:
        print('Chưa cài prophet. Chạy  !pip install prophet  rồi chạy lại ô này.')
        return None
    kq = []
    for sku in danh_sach_sku:
        g = d[(d['sku'] == sku) & (d['ngay'] <= het_chinh) & ~((d['het_hang'] == 1) & (d['so_luong_ban'] == 0))][['ngay', 'so_luong_ban']]
        g = g.rename(columns={'ngay': 'ds', 'so_luong_ban': 'y'})
        m = Prophet(weekly_seasonality=True, yearly_seasonality=False, daily_seasonality=False)
        m.fit(g)
        du_bao = m.predict(pd.DataFrame({'ds': ngay_thi}))
        kq.append(pd.DataFrame({'sku': sku, 'ngay': ngay_thi,
                                'du_doan_prophet': du_bao['yhat'].clip(lower=0).values}))
    return pd.concat(kq, ignore_index=True)

# ---------- GỌI HÀM P19 ----------
# (a) Croston/SBA cho mọi món
d = them_croston(d, HORIZON)
if 'du_doan_sba' in thi_d.columns:
    thi_d = thi_d.drop(columns=['du_doan_sba'])
thi_d = thi_d.merge(d[['sku', 'ngay', 'du_doan_sba']], on=['sku', 'ngay'], how='left')

# (b) Bảng so sánh trên TẤT CẢ món (không có Prophet)
cac_cach = ['du_doan_naive', 'du_doan_tb7', 'du_doan_sba', 'du_doan_lgbm']
thi_chung = thi_d.dropna(subset=cac_cach)
print(f'So sánh trên {len(thi_chung):,} dòng-ngày (cả 4 cách đều có số)')
diem_tat_ca = bang_diem_cho(thi_chung, cac_cach)
print(diem_tat_ca.round(3).to_string(index=False))

# (c) Prophet cho vài món tiêu biểu, so với các cách khác trên đúng các món đó
diem_prophet = None
if CHAY_PROPHET:
    sku_tieu_bieu = list(nhom['sku'].head(SO_SKU_PROPHET))
    kq_prophet = chay_prophet(d, sku_tieu_bieu, het_chinh, np.sort(thi['ngay'].unique()))
    if kq_prophet is not None:
        thi_p = thi_chung[thi_chung['sku'].isin(sku_tieu_bieu)].merge(kq_prophet, on=['sku', 'ngay'], how='left')
        diem_prophet = bang_diem_cho(thi_p.dropna(subset=['du_doan_prophet']), cac_cach + ['du_doan_prophet'])
        print(f'\nSo sánh có Prophet trên {len(sku_tieu_bieu)} món tiêu biểu:')
        print(diem_prophet.round(3).to_string(index=False))
