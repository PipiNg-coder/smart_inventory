# ---------- 1) Biểu đồ số thật và số dự đoán ----------
def ve_bieu_do(thi_chung, nhom, cac_cot, so_mon=3):
    """Số bán thật và số dự đoán, cộng theo từng tuần, cho các món doanh thu cao nhất."""
    top = list(nhom['sku'].head(so_mon))
    t = thi_chung[thi_chung['sku'].isin(top)].copy()
    t['tuan'] = t['ngay'].dt.to_period('W-SUN').dt.start_time
    fig, truc = plt.subplots(1, len(top), figsize=(5 * len(top), 3.6), squeeze=False)
    for ax, sku in zip(truc[0], top):
        g = t[t['sku'] == sku].groupby('tuan')[['so_luong_ban'] + cac_cot].sum()
        ax.plot(g.index, g['so_luong_ban'], 'k-o', label='Thật')
        for c in cac_cot:
            ax.plot(g.index, g[c], '--', label=c.replace('du_doan_', ''))
        ax.set_title(f'Món {sku}'); ax.tick_params(axis='x', rotation=45)
    truc[0][0].legend(); fig.suptitle('Số bán mỗi tuần: thật và dự đoán (phần thi)')
    fig.tight_layout(); plt.show()

ve_bieu_do(thi_chung, nhom, ['du_doan_tb7', 'du_doan_lgbm'])

# ---------- 2) Chọn cách thắng & Định tuyến thông minh theo phân khúc (Hybrid Routing) ----------
# Theo tư duy quản trị: Nhóm A, B hoặc Top 50 bắt buộc dùng LightGBM (GBM); 
# các nhóm còn lại cấp dưới mới để hệ thống đối chiếu và chọn phương án tối ưu (GBM hoặc SBA).
nhom_abc_map = nhom.set_index('sku')['nhom_abc'].to_dict()

# ---------- 3) Dự đoán 14 ngày tới (Hỗ trợ Định tuyến theo SKU / Nhóm ABC) ----------
def tao_khung_tuong_lai(d_goc, bang_le, horizon):
    """Thêm `horizon` ngày trống sau ngày cuối dữ liệu cho từng món (số bán chưa biết)."""
    ngay_moi = pd.date_range(d_goc['ngay'].max() + pd.Timedelta(days=1), periods=horizon)
    gia_cuoi = d_goc.sort_values('ngay').groupby('sku')['gia_ban'].last()
    ngay_le = set(pd.to_datetime(bang_le.loc[bang_le['la_le'] == 1, 'ngay']))
    khung = pd.DataFrame([(s, n) for s in d_goc['sku'].unique() for n in ngay_moi], columns=['sku', 'ngay'])
    khung['so_luong_ban'] = np.nan
    khung['het_hang'] = 0                      # giả định còn hàng
    khung['co_khuyen_mai'] = 0                 # giả định không khuyến mãi
    khung['la_le'] = khung['ngay'].isin(ngay_le).astype(int)
    khung['gia_ban'] = khung['sku'].map(gia_cuoi)
    return khung, ngay_moi

def du_doan_tuong_lai_hybrid(d_goc, bang_le, cach_mac_dinh, mo_hinh_cuoi, thong_tin_cuoi, horizon, top50_set, abc_map):
    """Định tuyến dự đoán 14 ngày tương lai:
    - Nhóm Top 50 hoặc Nhóm ABC (A, B): Ép 100% dùng LightGBM (du_doan_lgbm).
    - Các nhóm còn lại: Dùng cách tối ưu mặc định (cach_mac_dinh / SBA hoặc GBM)."""
    cot_goc = ['sku', 'ngay', 'so_luong_ban', 'het_hang', 'co_khuyen_mai', 'la_le', 'gia_ban']
    khung, ngay_moi = tao_khung_tuong_lai(d_goc, bang_le, horizon)
    tat_ca = pd.concat([d_goc[cot_goc], khung], ignore_index=True)
    tat_ca = tao_dac_trung(tat_ca, horizon)
    tat_ca = du_doan_baseline(tat_ca)
    tat_ca = them_croston(tat_ca, horizon)
    tl = tat_ca[tat_ca['ngay'].isin(ngay_moi)].copy()
    
    # Dự đoán bằng LightGBM cho toàn bộ khung
    pred_lgbm = mo_hinh_cuoi.predict(tao_ma_tran_x(tl, thong_tin_cuoi))
    # Dự đoán bằng cách mặc định (SBA hoặc cách thắng toàn cục)
    pred_default = tl[cach_mac_dinh].to_numpy() if cach_mac_dinh in tl.columns else pred_lgbm
    
    # Quy tắc định tuyến:
    # Nếu sku nằm trong top50_set hoặc thuộc nhom_abc trong ['A', 'B'] -> Dùng LightGBM
    # Ngược lại -> Dùng phương pháp mặc định (có thể là SBA cho hàng ngắt quãng)
    is_lgbm = tl['sku'].astype(str).isin(top50_set) | tl['sku'].map(abc_map).isin(['A', 'B'])
    
    tl['so_luong_du_doan'] = np.where(is_lgbm, pred_lgbm, pred_default)
    tl['so_luong_du_doan'] = tl['so_luong_du_doan'].fillna(0).clip(lower=0)
    tl['du_doan'] = tl['so_luong_du_doan']
    tl['cach_du_doan'] = np.where(is_lgbm, 'du_doan_lgbm', cach_mac_dinh)
    return tl[['sku', 'ngay', 'du_doan', 'so_luong_du_doan', 'cach_du_doan']].reset_index(drop=True)

def huan_luyen_cuoi(d, mo_hinh, hoc_d, chinh_d, thi_d):
    """Học lại trên TẤT CẢ dữ liệu dùng được (cả phần thi) với số cây đã tìm ở P18."""
    tat_ca_d = pd.concat([hoc_d, chinh_d, thi_d[hoc_d.columns]], ignore_index=True)
    tt = tinh_thong_tin_sku(tat_ca_d, d['sku'].unique())
    mh = lgb.LGBMRegressor(
        objective='poisson', n_estimators=max(int(mo_hinh.best_iteration_), 50), learning_rate=0.03,
        num_leaves=15, min_child_samples=20, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=1.0, random_state=42, verbose=-1)
    mh.fit(tao_ma_tran_x(tat_ca_d, tt), tat_ca_d['so_luong_ban'])
    return mh, tt

def doi_sang_du_doan_rong(du_doan_ngay):
    """Vector hóa: Từ bảng từng ngày sang đúng cột của bảng du_doan: sku, ngay_du_bao, du_bao_7, du_bao_30."""
    x = du_doan_ngay.sort_values(['sku', 'ngay']).copy()
    x['rn'] = x.groupby('sku').cumcount()
    du_bao_7 = x[x['rn'] < 7].groupby('sku')['so_luong_du_doan'].sum()
    thong_ke = x.groupby('sku').agg(
        ngay_du_bao=('ngay', lambda s: s.min().date()),
        tb_ngay=('so_luong_du_doan', 'mean')
    )
    thong_ke['du_bao_7'] = du_bao_7.reindex(thong_ke.index).fillna(0.0).astype(float)
    thong_ke['du_bao_30'] = (thong_ke['tb_ngay'] * 30.0).astype(float)
    return thong_ke.reset_index()[['sku', 'ngay_du_bao', 'du_bao_7', 'du_bao_30']]

# 00:00/12:00: huấn luyện mô hình cuối. 06:00/18:00: tải mô hình đã lưu.
if CHE_DO_PIPELINE == 'predict_only':
    _artifact = joblib.load('lightgbm_ton_kho.joblib')
    mo_hinh_cuoi = _artifact['mo_hinh']
    thong_tin_cuoi = _artifact['thong_tin']
    cach_thang_toan_cuc = _artifact.get('cach_thang', diem_tat_ca.iloc[0]['Cach'])
    print('Chế độ predict_only: dùng mô hình đã lưu, không huấn luyện lại.')
else:
    mo_hinh_cuoi, thong_tin_cuoi = huan_luyen_cuoi(d, mo_hinh, hoc_d, chinh_d, thi_d)
    cach_thang_toan_cuc = diem_tat_ca.iloc[0]['Cach']
cach_thang = cach_thang_toan_cuc
print(f'Cách thắng toàn cục theo {TIEU_CHI_CHON}: {cach_thang_toan_cuc}')

# Lưu bảng điểm mô hình
diem_luu = diem_tat_ca.assign(ngay_chay=pd.Timestamp.today().normalize())
luu_bigquery(diem_luu, 'diem_so_mo_hinh')

# Chạy định tuyến Hybrid: Top 50 & Nhóm A, B bắt buộc dùng LightGBM; còn lại theo cách tối ưu
du_doan_ngay = du_doan_tuong_lai_hybrid(d, bang_le, cach_thang_toan_cuc, mo_hinh_cuoi, thong_tin_cuoi, HORIZON, top50_sku, nhom_abc_map)
du_doan = doi_sang_du_doan_rong(du_doan_ngay)
du_doan['la_top50'] = du_doan['sku'].isin(top50_sku)             # đánh dấu 50 món top
du_doan_ngay['la_top50'] = du_doan_ngay['sku'].isin(top50_sku)

print('Số món top 50 có trong dự đoán:', int(du_doan['la_top50'].sum()))
print(f'Dự đoán {HORIZON} ngày: {du_doan_ngay["ngay"].min().date()} -> {du_doan_ngay["ngay"].max().date()}, {du_doan["sku"].nunique()} món')
print(du_doan.sort_values('du_bao_7', ascending=False).head(10).round(2).to_string(index=False))

# Thống kê số lượng SKU dùng LightGBM vs SBA trong bảng dự đoán ngày
thong_ke_cach = du_doan_ngay.drop_duplicates('sku')['cach_du_doan'].value_counts()
print('\n=== PHÂN BỔ SỐ LƯỢNG SKU THEO MÔ HÌNH DỰ ĐOÁN (HYBRID ROUTING) ===')
print(thong_ke_cach)

# ---------- 4) Ghi lên BigQuery ----------
du_doan_ngay_luu = du_doan_ngay.assign(ngay=du_doan_ngay['ngay'].dt.date, ngay_chay=pd.Timestamp.today().date())
luu_bigquery(du_doan_ngay_luu, 'du_doan_ngay', schema=[
    bigquery.SchemaField('sku', 'STRING'), bigquery.SchemaField('ngay', 'DATE'),
    bigquery.SchemaField('du_doan', 'FLOAT'), bigquery.SchemaField('so_luong_du_doan', 'FLOAT'),
    bigquery.SchemaField('cach_du_doan', 'STRING'), bigquery.SchemaField('ngay_chay', 'DATE'),
    bigquery.SchemaField('la_top50', 'BOOLEAN')])
luu_bigquery(du_doan, 'du_doan', schema=[
    bigquery.SchemaField('sku', 'STRING'), bigquery.SchemaField('ngay_du_bao', 'DATE'),
    bigquery.SchemaField('du_bao_7', 'FLOAT'), bigquery.SchemaField('du_bao_30', 'FLOAT'),
    bigquery.SchemaField('la_top50', 'BOOLEAN')])

# ---------- 5) Lưu mô hình ----------
joblib.dump({'mo_hinh': mo_hinh_cuoi, 'thong_tin': thong_tin_cuoi, 'horizon': HORIZON,
             'cach_du_doan': 'hybrid_lgbm_sba', 'cac_cot': CAC_COT_SO,
             'cach_thang': cach_thang_toan_cuc}, 'lightgbm_ton_kho.joblib')
print('Đã lưu file mô hình: lightgbm_ton_kho.joblib')
try:
    from google.colab import files
    files.download('lightgbm_ton_kho.joblib')
except ImportError:
    pass


import unicodedata
import numpy as np
import pandas as pd
from datetime import date
from lunardate import LunarDate

# ===== P20b-1: dựng lịch lễ từ bang_le_chuan =====
BANG_LE_CHUAN   = 'bang_le_chuan'
CUA_SO_MAC_DINH = (2, 0)       # (số ngày TRƯỚC lễ, số ngày SAU lễ) mà nhu cầu bị ảnh hưởng
CUA_SO_RIENG = {               # từ khóa trong tên dịp (viết thường) -> cửa sổ riêng. NHÓM TỰ CHỈNH
    'nguyên đán': (10, 3), 'tết dương': (2, 1), 'tình nhân': (3, 0), 'phụ nữ': (3, 0),
    'trung thu': (5, 0), 'giáng sinh': (3, 0), 'noel': (3, 0), 'nhà giáo': (2, 0),
}

def _chuan(s):
    return unicodedata.normalize('NFC', str(s)).strip().lower()

def _thang_ngay(v):
    if hasattr(v, 'month') and hasattr(v, 'day'):
        return int(v.month), int(v.day)
    p = str(v).strip().replace('/', '-').split('-')[-2:]
    return int(p[0]), int(p[1])

def _cua_so_cua(ten):
    t = _chuan(ten)
    for tu_khoa, cs in CUA_SO_RIENG.items():
        if tu_khoa in t:
            return cs
    return CUA_SO_MAC_DINH

def mo_rong_lich_le(le_chuan, nam_bd, nam_kt):
    """bang_le_chuan (ten_dip, ngay 'MM-DD', loai_lich) -> mỗi dịp một ngày cụ thể cho từng năm.
    Dịp Âm lịch được đổi sang ngày dương của từng năm."""
    rows = []
    for r in le_chuan.itertuples(index=False):
        thang, ngay = _thang_ngay(r.ngay)
        am = 'âm' in _chuan(r.loai_lich)
        truoc, sau = _cua_so_cua(r.ten_dip)
        for nam in range(nam_bd, nam_kt + 1):
            try:
                if am:
                    ld = LunarDate(nam, thang, ngay)
                    conv = getattr(ld, 'to_solar_date', None) or ld.toSolarDate
                    ng = conv()
                else:
                    ng = date(nam, thang, ngay)
            except ValueError:            # vd tháng Chạp không có ngày 30
                continue
            rows.append((r.ten_dip, pd.Timestamp(ng), r.loai_lich, truoc, sau))
    lich = pd.DataFrame(rows, columns=['ten_dip', 'ngay', 'loai_lich', 'truoc', 'sau'])
    return lich.sort_values('ngay').reset_index(drop=True)

def dung_cua_so(lich):
    """Mỗi dịp -> các ngày bị ảnh hưởng (từ `truoc` ngày trước đến `sau` ngày sau)."""
    out = []
    for r in lich.itertuples(index=False):
        for k in range(-int(r.truoc), int(r.sau) + 1):
            out.append((r.ten_dip, r.ngay, r.ngay + pd.Timedelta(days=k), k))
    return pd.DataFrame(out, columns=['ten_dip', 'ngay_le', 'ngay', 'lech'])

# ---------- GỌI: đọc bang_le_chuan, đổi thành ngày cụ thể ----------
le_chuan = client.query(f'SELECT ten_dip, ngay, loai_lich FROM `{PROJECT_ID}.{DATASET}.{BANG_LE_CHUAN}`').to_dataframe()
nam_bd = int(d['ngay'].min().year)
nam_kt = int((d['ngay'].max() + pd.Timedelta(days=HORIZON + 40)).year) + 1
lich = mo_rong_lich_le(le_chuan, nam_bd, nam_kt)
cua_so = dung_cua_so(lich)
print(f'bang_le_chuan: {len(le_chuan)} dịp -> {len(lich)} ngày lễ cụ thể ({nam_bd}-{nam_kt}), {len(cua_so)} ngày bị ảnh hưởng')
ngay_dau_dd = d['ngay'].max() + pd.Timedelta(days=1)
so_ngay_cu = (pd.Timestamp.today().normalize() - d['ngay'].max()).days
if so_ngay_cu > SO_NGAY_CU_CANH_BAO:
    print(f"CẢNH BÁO: dữ liệu bán cuối cùng là {d['ngay'].max().date()}, cũ {so_ngay_cu} ngày so với hôm nay. "
          f"Dự đoán tính từ {ngay_dau_dd.date()}, KHÔNG phải từ hôm nay. Nên xuất lại dữ liệu bán mới nhất rồi chạy lại.")
print('Dịp lễ rơi vào 30 ngày dự đoán tới:')
print(lich[lich['ngay'].between(ngay_dau_dd, ngay_dau_dd + pd.Timedelta(days=29))][['ten_dip', 'ngay', 'truoc', 'sau']].to_string(index=False))


# ===== P20b-2: hệ số tăng/giảm bán theo dịp lễ, học từ lịch sử =====
def tinh_he_so_le(d, lich, cua_so, nhom_map, k_on_dinh=30.0,
                  ngay_nen_truoc=28, ngay_nen_sau=14, min_ngay_nen=7, kep=(0.5, 3.0),
                  k_lan=1.0, dip_tuong_tu=None):
    """He so = (bán TB mỗi ngày trong cửa sổ lễ) / (bán TB mỗi ngày ở các ngày thường quanh đó).
    - Ngày hết hàng (ban_hieu_luc = NaN) bị bỏ qua.
    - Món bán ít thì số riêng của món thiếu tin cậy -> kéo về hệ số của nhóm hàng, rồi của cả cửa hàng.
      Trọng số của món = tong_ban_cua_so / (tong_ban_cua_so + k_on_dinh)."""
    ban = d.pivot_table(index='sku', columns='ngay', values='ban_hieu_luc', aggfunc='max')
    ban = ban.reindex(columns=pd.date_range(ban.columns.min(), ban.columns.max()))
    cot = ban.columns
    v = ban.to_numpy(float)
    trong_le = cot.isin(set(cua_so['ngay']))
    skus = ban.index.to_numpy()

    rows = []
    for r in lich.itertuples(index=False):
        tu, den = r.ngay - pd.Timedelta(days=int(r.truoc)), r.ngay + pd.Timedelta(days=int(r.sau))
        if tu < cot[0] or den > cot[-1]:
            continue                                  # dịp chưa được quan sát trọn vẹn
        m_le = (cot >= tu) & (cot <= den)
        m_nen = (((cot >= tu - pd.Timedelta(days=ngay_nen_truoc)) & (cot < tu)) |
                 ((cot > den) & (cot <= den + pd.Timedelta(days=ngay_nen_sau)))) & ~trong_le
        vl, vn = v[:, m_le], v[:, m_nen]
        sl, cl = np.nansum(vl, axis=1), (~np.isnan(vl)).sum(axis=1)
        sn, cn = np.nansum(vn, axis=1), (~np.isnan(vn)).sum(axis=1)
        ok = (cl >= 1) & (cn >= min_ngay_nen)
        with np.errstate(divide='ignore', invalid='ignore'):
            tb_le, tb_nen = sl / cl, sn / cn
        ok &= tb_nen > 0
        rows.append(pd.DataFrame({'sku': skus[ok], 'ten_dip': r.ten_dip, 'ngay_le': r.ngay,
                                  'tb_le': tb_le[ok], 'tb_nen': tb_nen[ok], 'tong_cua_so': sl[ok]}))
    cols = ['sku', 'ten_dip', 'ngay_le', 'tb_le', 'tb_nen', 'tong_cua_so']
    qs = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=cols)
    qs['nhom'] = qs['sku'].map(nhom_map).fillna('khac')

    def gop(df, khoa):
        g = df.groupby(khoa).agg(n=('ngay_le', 'nunique'), s_le=('tb_le', 'sum'),
                                 s_nen=('tb_nen', 'sum'), tong=('tong_cua_so', 'sum')).reset_index()
        g['he'] = g['s_le'] / g['s_nen']
        return g
    g_sku, g_nhom, g_chung = gop(qs, ['sku', 'ten_dip']), gop(qs, ['nhom', 'ten_dip']), gop(qs, ['ten_dip'])

    full = pd.MultiIndex.from_product([skus, sorted(lich['ten_dip'].unique())],
                                      names=['sku', 'ten_dip']).to_frame(index=False)
    full['nhom'] = full['sku'].map(nhom_map).fillna('khac')
    full = (full.merge(g_sku[['sku', 'ten_dip', 'n', 'tong', 'he']].rename(
                        columns={'n': 'so_lan_quan_sat', 'tong': 'tong_ban_cua_so', 'he': 'he_so_sku'}), how='left')
                .merge(g_nhom[['nhom', 'ten_dip', 'he']].rename(columns={'he': 'he_so_nhom'}), how='left')
                .merge(g_chung[['ten_dip', 'n', 'he']].rename(columns={'n': 'so_lan_chung', 'he': 'he_so_chung'}),
                       how='left'))
    full['so_lan_quan_sat'] = full['so_lan_quan_sat'].fillna(0).astype(int)
    full['so_lan_chung'] = full['so_lan_chung'].fillna(0).astype(int)
    full['tong_ban_cua_so'] = full['tong_ban_cua_so'].fillna(0.0)
    nen = full['he_so_nhom'].fillna(full['he_so_chung'])             # mốc để kéo về
    w = full['tong_ban_cua_so'] / (full['tong_ban_cua_so'] + k_on_dinh)
    he = (w * full['he_so_sku'].fillna(0) + (1 - w) * nen.fillna(1.0)).where(full['he_so_sku'].notna(), nen)
    full['he_so_goc'] = he.fillna(1.0).clip(*kep)
    lan = full['so_lan_chung']
    # co về 1 theo số lần quan sát: thấy 1 lần với k_lan=1 thì chỉ giữ 50% mức tăng/giảm
    full['he_so'] = 1 + (full['he_so_goc'] - 1) * (lan / (lan + k_lan) if k_lan > 0 else 1.0)
    full['nguon'] = np.where(full['so_lan_chung'] == 0, 'khong_co_du_lieu',
                     np.where(full['he_so_sku'].notna() & (w >= 0.5), 'sku',
                     np.where(full['he_so_nhom'].notna(), 'nhom', 'chung')))
    # CAO: số riêng của món, thấy dịp này >= 2 lần | TRUNG_BINH: số riêng của món nhưng chỉ thấy 1 lần
    # THAP: món bán ít nên mượn số của nhóm hàng / cả cửa hàng
    full['do_tin_cay'] = np.where(full['nguon'] == 'khong_co_du_lieu', 'KHONG_CO_DU_LIEU',
                          np.where((full['nguon'] == 'sku') & (full['so_lan_quan_sat'] >= 2), 'CAO',
                          np.where(full['nguon'] == 'sku', 'TRUNG_BINH', 'THAP')))
    full.loc[full['nguon'] == 'khong_co_du_lieu', 'he_so'] = 1.0   # chưa từng thấy dịp này: không đoán bừa
    # tùy chọn (nhóm quyết định): dịp chưa có lịch sử mượn hệ số dịp tương tự
    for dich_kw, nguon_kw in (dip_tuong_tu or {}).items():
        ten_d = [t for t in full['ten_dip'].unique() if _chuan(dich_kw) in _chuan(t)]
        ten_n = [t for t in full['ten_dip'].unique() if _chuan(nguon_kw) in _chuan(t)]
        if not ten_d or not ten_n:
            print(f'DIP_TUONG_TU: không khớp tên dịp {dich_kw!r} -> {nguon_kw!r}, bỏ qua')
            continue
        src = full[(full['ten_dip'] == ten_n[0]) & (full['so_lan_chung'] > 0)].set_index('sku')['he_so']
        mask = (full['ten_dip'] == ten_d[0]) & (full['so_lan_chung'] == 0)
        full.loc[mask, 'he_so'] = full.loc[mask, 'sku'].map(src).fillna(1.0)
        full.loc[mask, 'nguon'] = 'dip_tuong_tu'
        full.loc[mask, 'do_tin_cay'] = 'MUON_DIP_TUONG_TU'
    return full[['sku', 'ten_dip', 'so_lan_quan_sat', 'so_lan_chung', 'tong_ban_cua_so', 'he_so_sku',
                 'he_so_nhom', 'he_so_chung', 'he_so_goc', 'he_so', 'nguon', 'do_tin_cay']], qs

# ---------- GỌI: học hệ số lễ ----------
try:
    dim = client.query(f'SELECT CAST(sku AS STRING) AS sku, ANY_VALUE(nhom) AS nhom FROM `{PROJECT_ID}.{DATASET}.dim_product` GROUP BY sku').to_dataframe()
    nhom_map = dim.set_index('sku')['nhom'].to_dict()
except Exception as e:
    print('Không đọc được dim_product.nhom, coi cả cửa hàng là 1 nhóm:', e)
    nhom_map = {}

he_so_le, quan_sat_le = tinh_he_so_le(d, lich, cua_so, nhom_map, k_lan=K_LAN_QUAN_SAT, dip_tuong_tu=DIP_TUONG_TU)
print('=== HỆ SỐ THEO DỊP (trung bình các món; 1.0 = bán như ngày thường) ===')
tt = (he_so_le.groupby('ten_dip').agg(so_lan_thay=('so_lan_chung', 'max'), he_so_tb=('he_so', 'mean'),
                                       so_mon_tin_cay_thap=('do_tin_cay', lambda s: int((s == 'THAP').sum())))
      .sort_values('he_so_tb', ascending=False))
print(tt.round(2).to_string())
print(f'(Hệ số đã co về 1 theo số lần thấy, K_LAN_QUAN_SAT={K_LAN_QUAN_SAT}; cột he_so_goc là số chưa co.)')
print('Dịp có so_lan_thay = 0 thì dữ liệu chưa từng có dịp đó: hệ số = 1.0 và độ tin cậy KHONG_CO_DU_LIEU (không đoán bừa).')


# ===== P20b-3: dự đoán 7 và 30 ngày CÓ tính ngày lễ =====
def du_doan_co_le(du_doan_ngay, cua_so, he_so, gia_map, so_ngay=30):
    """du_doan_ngay: bảng P20 (sku, ngay, so_luong_du_doan) - chỉ HORIZON ngày đầu.
    Ngày 1..HORIZON lấy từ mô hình; các ngày sau lấy trung bình 'ngày thường' của chính món đó."""
    ngay_dau = du_doan_ngay['ngay'].min()
    ngay_all = pd.date_range(ngay_dau, periods=so_ngay)
    sku_all = du_doan_ngay['sku'].unique()
    f = pd.MultiIndex.from_product([sku_all, ngay_all], names=['sku', 'ngay']).to_frame(index=False)
    f['ngay_thu'] = (f['ngay'] - ngay_dau).dt.days + 1
    f = f.merge(du_doan_ngay[['sku', 'ngay', 'so_luong_du_doan']], on=['sku', 'ngay'], how='left')

    trong_le = f['ngay'].isin(set(cua_so['ngay']))
    f['thu'] = f['ngay'].dt.dayofweek
    ok_thuong = f['so_luong_du_doan'].notna() & ~trong_le
    tb_sku = (f[ok_thuong].groupby('sku')['so_luong_du_doan'].mean()
              .reindex(sku_all).fillna(f.groupby('sku')['so_luong_du_doan'].mean()))
    tb_thu = f[ok_thuong].groupby(['sku', 'thu'])['so_luong_du_doan'].mean()
    gt = pd.Series(tb_thu.reindex(pd.MultiIndex.from_frame(f[['sku', 'thu']])).to_numpy(), index=f.index)
    # ngày sau HORIZON: trung bình ngày thường của món đúng thứ đó trong tuần; thiếu thì dùng trung bình cả món
    f['du_nen'] = f['so_luong_du_doan'].fillna(gt).fillna(f['sku'].map(tb_sku)).fillna(0.0)
    f = f.drop(columns='thu')

    m = (f.merge(cua_so[['ngay', 'ten_dip', 'ngay_le']], on='ngay', how='left')
           .merge(he_so[['sku', 'ten_dip', 'he_so', 'do_tin_cay']], on=['sku', 'ten_dip'], how='left'))
    m['he_so'] = m['he_so'].fillna(1.0)
    chi_tiet = m[m['ten_dip'].notna()].copy()                       # mọi (món, ngày, dịp) có dính lễ
    m['lech'] = (m['he_so'] - 1).abs()
    ngay_df = (m.sort_values(['sku', 'ngay', 'lech'], ascending=[True, True, False])
                 .drop_duplicates(['sku', 'ngay']).sort_values(['sku', 'ngay']).reset_index(drop=True))
    ngay_df['du_co_le'] = ngay_df['du_nen'] * ngay_df['he_so']       # ngày dính 2 dịp: lấy dịp tác động mạnh nhất
    ngay_df['gia_ban'] = ngay_df['sku'].map(gia_map)
    ngay_df['dt_nen'] = ngay_df['du_nen'] * ngay_df['gia_ban']
    ngay_df['dt_co_le'] = ngay_df['du_co_le'] * ngay_df['gia_ban']
    ngay_df.loc[ngay_df['ten_dip'].isna(), ['ten_dip', 'do_tin_cay']] = [None, None]
    ngay_df = ngay_df.drop(columns=['lech', 'so_luong_du_doan'])

    # --- bảng 1: mỗi món, mỗi dịp lễ trong khoảng dự đoán ---
    chi_tiet = chi_tiet.merge(ngay_df[['sku', 'ngay', 'du_co_le', 'gia_ban']], on=['sku', 'ngay'])
    chi_tiet['du_co_le'] = chi_tiet['du_co_le']
    chi_tiet['dt_nen'] = chi_tiet['du_nen'] * chi_tiet['gia_ban']
    # số lượng/doanh thu "có lễ" riêng của dịp này (không lẫn dịp khác)
    chi_tiet['du_co_le_dip'] = chi_tiet['du_nen'] * chi_tiet['he_so']
    chi_tiet['dt_co_le_dip'] = chi_tiet['du_co_le_dip'] * chi_tiet['gia_ban']
    theo_dip = (chi_tiet.groupby(['sku', 'ten_dip', 'ngay_le'], as_index=False)
                .agg(ngay_bat_dau=('ngay', 'min'), so_ngay_anh_huong=('ngay', 'count'),
                     ngay_thu_dau=('ngay_thu', 'min'),
                     du_bao_nen=('du_nen', 'sum'), du_bao_co_le=('du_co_le_dip', 'sum'),
                     doanh_thu_nen=('dt_nen', 'sum'), doanh_thu_co_le=('dt_co_le_dip', 'sum'),
                     he_so=('he_so', 'first'), do_tin_cay=('do_tin_cay', 'first')))
    theo_dip['chenh_lech_so_luong'] = theo_dip['du_bao_co_le'] - theo_dip['du_bao_nen']
    theo_dip['chenh_lech_doanh_thu'] = theo_dip['doanh_thu_co_le'] - theo_dip['doanh_thu_nen']
    theo_dip['trong_7_ngay'] = theo_dip['ngay_thu_dau'] <= 7
    theo_dip = theo_dip.drop(columns='ngay_thu_dau')

    # --- bảng 2: mỗi món một dòng, tổng 7 và 30 ngày ---
    def tong(h):
        x = ngay_df[ngay_df['ngay_thu'] <= h]
        g = x.groupby('sku').agg(nen=('du_nen', 'sum'), co_le=('du_co_le', 'sum'),
                                 dt_nen=('dt_nen', 'sum'), dt_co_le=('dt_co_le', 'sum'))
        g['dip'] = x.dropna(subset=['ten_dip']).groupby('sku')['ten_dip'].agg(lambda s: ', '.join(dict.fromkeys(s)))
        return g.rename(columns=lambda c: f'{c}_{h}')
    rong = tong(7).join(tong(30)).reset_index()
    rong = rong.rename(columns={'nen_7': 'du_bao_7_nen', 'co_le_7': 'du_bao_7_co_le', 'dt_nen_7': 'doanh_thu_7_nen',
                                'dt_co_le_7': 'doanh_thu_7_co_le', 'dip_7': 'dip_trong_7',
                                'nen_30': 'du_bao_30_nen', 'co_le_30': 'du_bao_30_co_le',
                                'dt_nen_30': 'doanh_thu_30_nen', 'dt_co_le_30': 'doanh_thu_30_co_le',
                                'dip_30': 'dip_trong_30'})
    rong.insert(1, 'ngay_du_bao', ngay_dau.date())
    ngay_df = ngay_df.rename(columns={'du_nen': 'du_doan_nen', 'du_co_le': 'du_doan_co_le'})
    return ngay_df, theo_dip, rong

# ---------- GỌI: dự đoán có lễ ----------
gia_map = d.sort_values('ngay').groupby('sku')['gia_ban'].last()
du_doan_ngay_le, du_doan_theo_dip, du_doan_le = du_doan_co_le(du_doan_ngay, cua_so, he_so_le, gia_map, so_ngay=30)
for t in (du_doan_ngay_le, du_doan_theo_dip, du_doan_le):
    t['la_top50'] = t['sku'].isin(top50_sku)
print(f'du_doan_le: {len(du_doan_le)} món | du_doan_theo_dip: {len(du_doan_theo_dip)} dòng (món x dịp)')
print('\n=== TOP 10 MÓN TĂNG NHIỀU NHẤT NHỜ DỊP LỄ TRONG 30 NGÀY TỚI ===')
x = du_doan_theo_dip.sort_values('chenh_lech_doanh_thu', ascending=False).head(10)
print(x[['sku', 'ten_dip', 'ngay_le', 'du_bao_nen', 'du_bao_co_le', 'doanh_thu_nen', 'doanh_thu_co_le', 'he_so', 'do_tin_cay']].round(1).to_string(index=False))

# ---------- Ghi lên BigQuery (tự gắn hậu tố _v2) ----------
def _ngay(df, cols):
    df = df.copy()
    for c in cols:
        df[c] = pd.to_datetime(df[c]).dt.date
    return df
luu_bigquery(_ngay(lich, ['ngay']).assign(ngay_chay=pd.Timestamp.today().date()), 'lich_le_ngay')
luu_bigquery(he_so_le, 'he_so_le')
luu_bigquery(_ngay(du_doan_ngay_le.fillna({'ten_dip': '', 'do_tin_cay': ''}), ['ngay', 'ngay_le']).drop(columns=['ngay_le']), 'du_doan_ngay_le')
luu_bigquery(_ngay(du_doan_theo_dip, ['ngay_le', 'ngay_bat_dau']), 'du_doan_theo_dip')
luu_bigquery(du_doan_le.fillna({'dip_trong_7': '', 'dip_trong_30': ''}), 'du_doan_le')

# Đồng bộ bảng du_doan_v2 với số dự báo ĐÃ TÍNH LỄ (để P21, P22 và Web gọi bảng nào cũng khớp)
du_doan = du_doan_le[['sku', 'ngay_du_bao', 'du_bao_7_co_le', 'du_bao_30_co_le',
                      'du_bao_7_nen', 'du_bao_30_nen', 'doanh_thu_7_co_le', 'doanh_thu_30_co_le',
                      'dip_trong_7', 'dip_trong_30', 'la_top50']].rename(
    columns={'du_bao_7_co_le': 'du_bao_7', 'du_bao_30_co_le': 'du_bao_30'}
).fillna({'dip_trong_7': '', 'dip_trong_30': ''})
luu_bigquery(du_doan, 'du_doan')


# ---------- P21: TÍNH BẢNG CHÍNH SÁCH (SS, ROP, EOQ, MIN, MAX) ----------
def tao_bang_chinh_sach(thi_d, cach_thang, du_doan, nhom, client):
    # 1. Tính độ lệch chuẩn sai số dự báo (sigma) trên phần THI cho từng món
    thi_hop_le = thi_d.dropna(subset=['so_luong_ban', cach_thang]).copy()
    thi_hop_le['sai_so'] = thi_hop_le['so_luong_ban'] - thi_hop_le[cach_thang]
    sigma_sku = thi_hop_le.groupby('sku')['sai_so'].std(ddof=1).rename('sigma')

    # 2. Lấy dự báo bán mỗi ngày và nhu cầu năm từ bảng du_doan (P20)
    cs = du_doan[['sku', 'du_bao_7']].copy()
    cs['nhu_cau_ngay'] = cs['du_bao_7'] / 7.0
    cs['nhu_cau_nam']  = cs['nhu_cau_ngay'] * 365.0

    # 3. Ghép với nhóm ABC-XYZ (P16) và sai số sigma
    cs = cs.merge(nhom[['sku', 'nhom_abcxyz']], on='sku', how='left')
    cs['nhom_abcxyz'] = cs['nhom_abcxyz'].fillna('CZ')
    cs = cs.merge(sigma_sku, on='sku', how='left')
    cs['sigma'] = cs['sigma'].fillna(cs['nhu_cau_ngay'] * 0.30).clip(lower=0.0)

    # 4. Lấy Giá vốn (gia_von) và Số ngày chờ hàng (so_ngay_cho_hang) từ BigQuery
    try:
        sql_dim = f"""
            SELECT CAST(sku AS STRING) AS sku,
                   MAX(CAST(gia_von AS FLOAT64)) AS gia_von,
                   MAX(CAST(so_ngay_cho_hang AS FLOAT64)) AS lead_time
            FROM `{PROJECT_ID}.{DATASET}.dim_product`
            GROUP BY sku
        """
        df_prod = client.query(sql_dim).to_dataframe()
        print('Đã lấy thông tin gia_von và so_ngay_cho_hang từ mart_data.dim_product.')
    except Exception:
        try:
            sql_raw_prod = f"""
                SELECT UPPER(TRIM(CAST(product_id AS STRING))) AS sku,
                       MAX(CAST(cost_price AS FLOAT64)) AS gia_von
                FROM `{PROJECT_ID}.raw_data.product`
                GROUP BY sku
            """
            df_prod = client.query(sql_raw_prod).to_dataframe()
            df_prod['lead_time'] = 3.0
            print('Chưa có dim_product, đã lấy gia_von từ raw_data.product (mặc định lead_time = 3 ngày).')
        except Exception:
            df_prod = pd.DataFrame({'sku': cs['sku'], 'gia_von': 10000.0, 'lead_time': 3.0})
            print('Dùng thông số mặc định: gia_von = 10.000đ, lead_time = 3 ngày.')

    cs = cs.merge(df_prod, on='sku', how='left')
    cs['lead_time']   = cs['lead_time'].fillna(3.0).clip(lower=1.0)
    cs['gia_von']     = cs['gia_von'].fillna(10000.0).clip(lower=1000.0)
    cs['chi_phi_dat'] = 20000.0                  # S = 20.000 đồng / lần đặt hàng
    cs['chi_phi_luu'] = cs['gia_von'] * 0.20     # H = 20% giá vốn mỗi năm

    # 5. Tra hệ số Z theo nhóm ABC (A: 98%, B: 95%, C: 90%)
    z_map = {'A': 2.053749, 'B': 1.644854, 'C': 1.281552}
    cs['Z'] = cs['nhom_abcxyz'].str[0].map(z_map).fillna(1.644854)

    # 6. Tính SS, ROP, EOQ, Min, Max theo đúng công thức tối ưu
    cs['ss']  = np.ceil(cs['Z'] * cs['sigma'] * np.sqrt(cs['lead_time'])).astype(int)
    cs['rop'] = np.ceil(cs['nhu_cau_ngay'] * cs['lead_time'] + cs['ss']).astype(int)
    cs['eoq'] = np.where(
        cs['chi_phi_luu'] > 0,
        np.ceil(np.sqrt((2.0 * cs['nhu_cau_nam'] * cs['chi_phi_dat']) / cs['chi_phi_luu'])),
        0
    ).astype(int)
    cs['min'] = cs['rop']
    cs['max'] = cs['min'] + cs['eoq']

    return cs[['sku', 'nhom_abcxyz', 'ss', 'rop', 'eoq', 'min', 'max']]

# ---------- GỌI HÀM P21 VÀ GHI BẢNG MỚI LÊN BIGQUERY ----------
# Dùng số dự báo 7 ngày ĐÃ TÍNH LỄ (du_bao_7_co_le) để tính ROP, EOQ, Min, Max chuẩn mùa lễ
du_doan_cho_cs = du_doan_le[['sku', 'du_bao_7_co_le']].rename(columns={'du_bao_7_co_le': 'du_bao_7'})
chinh_sach = tao_bang_chinh_sach(thi_d, cach_thang, du_doan_cho_cs, nhom, client)
print('=== 10 DÒNG ĐẦU BẢNG CHÍNH SÁCH MỚI (P21) ===')
print(chinh_sach.head(10).to_string(index=False))

luu_bigquery(chinh_sach, 'chinh_sach', schema=[
    bigquery.SchemaField('sku', 'STRING'),
    bigquery.SchemaField('nhom_abcxyz', 'STRING'),
    bigquery.SchemaField('ss', 'INTEGER'),
    bigquery.SchemaField('rop', 'INTEGER'),
    bigquery.SchemaField('eoq', 'INTEGER'),
    bigquery.SchemaField('min', 'INTEGER'),
    bigquery.SchemaField('max', 'INTEGER'),
])

ten_csv = f'chinh_sach_mart{HAU_TO_BANG}.csv'
chinh_sach.to_csv(ten_csv, index=False)
print(f'Đã lưu file {ten_csv} thành công!')


NGUONG_TON_NHIEU_NGAY = 60   # tồn đủ bán hơn 60 ngày thì coi là tồn quá nhiều (nhóm tự chọn con số này)

def sql_select(nguon_cs, nguon_dd_le, nguon_ton, nguong=60):
    """Câu SELECT tính cảnh báo tích hợp: Dự báo Có Lễ (P20b) + Hàng Đang Về + Tiền Đọng Vốn."""
    return f"""
WITH cs AS (   -- chính sách (P21)
  SELECT sku, nhom_abcxyz, ss, eoq, `min` AS ton_toi_thieu, `max` AS ton_toi_da
  FROM {nguon_cs}
), dd AS (     -- dự đoán CÓ LỄ (P20b - du_doan_le_v2)
  SELECT sku,
         du_bao_7_co_le AS du_bao_7,
         du_bao_30_co_le AS du_bao_30,
         du_bao_7_nen,
         du_bao_30_nen,
         doanh_thu_7_co_le,
         doanh_thu_30_co_le,
         IFNULL(NULLIF(dip_trong_7, ''), 'Không có') AS dip_trong_7,
         IFNULL(NULLIF(dip_trong_30, ''), 'Không có') AS dip_trong_30,
         la_top50
  FROM {nguon_dd_le}
), dp AS (     -- Kéo giá vốn từ dim_product để tính tiền đọng vốn
  SELECT CAST(sku AS STRING) AS sku, MAX(CAST(gia_von AS FLOAT64)) AS gia_von
  FROM `{PROJECT_ID}.{DATASET}.dim_product`
  GROUP BY sku
), t AS (
  SELECT cs.sku, cs.nhom_abcxyz, dd.la_top50, dp.gia_von,
         IFNULL(h.ton_cuoi_ngay, 0) AS ton_hien_tai,
         IFNULL(h.hang_dang_ve, 0) AS hang_dang_ve,
         -- LÕI NGHIỆP VỤ: Tồn khả dụng = Tại kho + Đang trên đường về
         (IFNULL(h.ton_cuoi_ngay, 0) + IFNULL(h.hang_dang_ve, 0)) AS ton_kha_dung,
         cs.ton_toi_thieu, cs.ton_toi_da, cs.ss,
         dd.du_bao_7, dd.du_bao_30,
         dd.du_bao_7_nen, dd.du_bao_30_nen,
         dd.doanh_thu_7_co_le, dd.doanh_thu_30_co_le,
         dd.dip_trong_7, dd.dip_trong_30,
         SAFE_DIVIDE(dd.du_bao_30, 30) AS nhu_cau_ngay            -- số bán mỗi ngày (có tính lễ)
  FROM cs
  JOIN dd USING (sku)
  LEFT JOIN {nguon_ton} h USING (sku)
  LEFT JOIN dp USING (sku)
), x AS (
  SELECT *,
         SAFE_DIVIDE(ton_hien_tai, nhu_cau_ngay) AS so_ngay_ton_kho,
         (ton_kha_dung <= ton_toi_thieu AND ton_toi_thieu > 0)  AS cham_ton_toi_thieu,
         (ton_kha_dung < du_bao_7)                              AS het_hang_7_ngay,
         (ton_kha_dung < du_bao_30)                             AS het_hang_30_ngay,
         -- Dùng TỒN KHẢ DỤNG để tính số lượng cần nhập thêm, tránh đặt đúp
         CAST(CEIL(GREATEST(0, du_bao_7  + ss - ton_kha_dung)) AS INT64) AS nen_nhap_du_7_ngay,
         CAST(CEIL(GREATEST(0, du_bao_30 + ss - ton_kha_dung)) AS INT64) AS nen_nhap_du_30_ngay,
         CAST(GREATEST(0, ton_toi_da - ton_kha_dung) AS INT64)           AS nen_nhap_toi_muc_toi_da,
         COALESCE(SAFE_DIVIDE(ton_hien_tai, nhu_cau_ngay) > {nguong}, nhu_cau_ngay = 0 AND ton_hien_tai > ton_toi_da) AS ton_qua_nhieu,
         CAST(GREATEST(0, ton_hien_tai - CEIL({nguong} * IFNULL(nhu_cau_ngay, 0))) AS INT64) AS so_luong_nen_xa
  FROM t
), y AS (
  SELECT *,
         CASE WHEN ton_hien_tai = 0 AND nhu_cau_ngay > 0 THEN 'DA_HET_HANG'
              WHEN het_hang_7_ngay                       THEN 'SAP_HET_7_NGAY'
              WHEN cham_ton_toi_thieu                    THEN 'CHAM_MUC_TOI_THIEU'
              WHEN het_hang_30_ngay                      THEN 'SAP_HET_30_NGAY'
              WHEN ton_qua_nhieu                         THEN 'TON_QUA_NHIEU'
              ELSE 'BINH_THUONG' END AS muc_canh_bao
  FROM x
)
SELECT sku, nhom_abcxyz, la_top50, muc_canh_bao,
       ton_hien_tai, hang_dang_ve, ton_kha_dung, ton_toi_thieu, ton_toi_da,
       ROUND(du_bao_7, 1) AS du_bao_7, ROUND(du_bao_30, 1) AS du_bao_30,
       ROUND(du_bao_7_nen, 1) AS du_bao_7_nen, ROUND(du_bao_30_nen, 1) AS du_bao_30_nen,
       ROUND(doanh_thu_7_co_le, 0) AS doanh_thu_7_co_le, ROUND(doanh_thu_30_co_le, 0) AS doanh_thu_30_co_le,
       dip_trong_7, dip_trong_30,
       ROUND(so_ngay_ton_kho, 1) AS so_ngay_ton_kho,
       cham_ton_toi_thieu, het_hang_7_ngay, het_hang_30_ngay,
       nen_nhap_du_7_ngay, nen_nhap_du_30_ngay,
       CASE WHEN muc_canh_bao IN ('DA_HET_HANG', 'SAP_HET_7_NGAY', 'CHAM_MUC_TOI_THIEU')
                 THEN GREATEST(nen_nhap_toi_muc_toi_da, nen_nhap_du_7_ngay)
            WHEN muc_canh_bao = 'SAP_HET_30_NGAY' THEN nen_nhap_du_30_ngay
            ELSE 0 END AS nen_nhap_goi_y,
       ton_qua_nhieu,
       so_luong_nen_xa,                                        -- Vẫn giữ nguyên cột đếm số lượng (Dư 500 cái)
       (so_luong_nen_xa * IFNULL(gia_von, 0)) AS tien_dong_von -- Thêm một cột mới tính ra tiền (Kẹt 15 triệu)
FROM y
""".strip()


def tao_view_canh_bao(client, hau_to=HAU_TO_BANG, nguong=NGUONG_TON_NHIEU_NGAY):
    """Tạo (hoặc làm mới) view cảnh báo từ chinh_sach_v2, du_doan_le_v2 (có lễ) và ton_hien_tai."""
    p = f'{PROJECT_ID}.{DATASET}'
    ten_view = f'{p}.canh_bao_ton_kho{hau_to}'
    cau_select = sql_select(f'`{p}.chinh_sach{hau_to}`', f'`{p}.du_doan_le{hau_to}`', f'`{p}.ton_hien_tai`', nguong)
    client.query(f'CREATE OR REPLACE VIEW `{ten_view}` AS\n{cau_select}').result()
    print(f'Đã tạo view {ten_view}')
    return ten_view

# ---------- GỌI HÀM P22 ----------
ten_view = tao_view_canh_bao(client)

tong_hop = client.query(f'''
    SELECT muc_canh_bao, COUNT(*) AS so_mon, SUM(CAST(la_top50 AS INT64)) AS trong_top50,
           SUM(nen_nhap_goi_y) AS tong_nen_nhap, SUM(so_luong_nen_xa) AS tong_nen_xa,
           SUM(tien_dong_von) AS tong_tien_dong_von
    FROM `{ten_view}` GROUP BY muc_canh_bao ORDER BY so_mon DESC''').to_dataframe()
print('=== TỔNG HỢP CẢNH BÁO ===')
pd.options.display.float_format = '{:,.0f}'.format
print(tong_hop.to_string(index=False))

can_xu_ly = client.query(f'''
    SELECT sku, nhom_abcxyz, la_top50, muc_canh_bao, ton_kha_dung, du_bao_7, doanh_thu_7_co_le, dip_trong_7, nen_nhap_goi_y
    FROM `{ten_view}` WHERE muc_canh_bao IN ('DA_HET_HANG', 'SAP_HET_7_NGAY', 'CHAM_MUC_TOI_THIEU')
    ORDER BY la_top50 DESC, nen_nhap_goi_y DESC LIMIT 10''').to_dataframe()
print('\n=== 10 MÓN CẦN NHẬP GẤP (ưu tiên top 50, kèm dịp lễ & doanh thu dự kiến) ===')
print(can_xu_ly.to_string(index=False))


# ---------- SO SÁNH BẢNG CÙNG TÊN (_v2) GIỮA DATASET CŨ (mart_data) VÀ DATASET MỚI (mart_data_v2) ----------
def so_sanh_cu_moi(ten_goc, khoa_chinh, cot_so_sanh, hau_to=HAU_TO_BANG):
    ten_bang = f'{ten_goc}{hau_to}'
    bang_cu  = f'{PROJECT_ID}.{DATASET_CU}.{ten_bang}'
    bang_moi = f'{PROJECT_ID}.{DATASET}.{ten_bang}'
    print(f'\n================ SO SÁNH: {DATASET_CU}.{ten_bang} (CŨ) vs {DATASET}.{ten_bang} (MỚI) ================')
    try:
        df_cu  = client.query(f'SELECT * FROM `{bang_cu}`').to_dataframe()
    except Exception:
        print(f'-> Bảng cũ `{bang_cu}` chưa tồn tại trên BigQuery.')
        return
    df_moi = client.query(f'SELECT * FROM `{bang_moi}`').to_dataframe()
    print(f'Số dòng -> Bản cũ ({DATASET_CU}): {len(df_cu):,} dòng | Bản mới ({DATASET}): {len(df_moi):,} dòng')

    g = df_cu.merge(df_moi, on=khoa_chinh, suffixes=('_cu', '_moi'), how='outer')
    for c in cot_so_sanh:
        c_cu, c_moi = f'{c}_cu', f'{c}_moi'
        if c_cu in g.columns and c_moi in g.columns:
            if pd.api.types.is_numeric_dtype(g[c_moi]):
                lech = (g[c_moi].fillna(0) - g[c_cu].fillna(0)).abs().max()
                print(f'  - Cột `{c}`: Độ lệch tuyệt đối lớn nhất = {lech:.4f}')
            else:
                khop = (g[c_cu].astype(str) == g[c_moi].astype(str)).mean() * 100
                print(f'  - Cột `{c}`: Tỷ lệ khớp = {khop:.2f}%')
    display_cols = khoa_chinh + [f'{c}_{s}' for c in cot_so_sanh for s in ('cu', 'moi') if f'{c}_{s}' in g.columns]
    print('\n5 dòng đối chiếu đầu tiên:')
    print(g[display_cols].head(5).to_string(index=False))

so_sanh_cu_moi('nhom_abcxyz',      ['sku'], ['nhom_abcxyz', 'cv_tuan'])
so_sanh_cu_moi('diem_so_mo_hinh',  ['Cach'], ['MAE', 'RMSE', 'WAPE', 'WAPE_tuan', 'Lech_tong'])
so_sanh_cu_moi('du_doan',          ['sku'], ['du_bao_7', 'du_bao_30'])
so_sanh_cu_moi('du_doan_le',       ['sku'], ['du_bao_7_co_le', 'du_bao_30_co_le', 'doanh_thu_7_co_le'])
so_sanh_cu_moi('chinh_sach',       ['sku'], ['nhom_abcxyz', 'ss', 'rop', 'eoq', 'min', 'max'])
so_sanh_cu_moi('canh_bao_ton_kho', ['sku'], ['muc_canh_bao', 'ton_kha_dung', 'nen_nhap_goi_y', 'tien_dong_von'])
