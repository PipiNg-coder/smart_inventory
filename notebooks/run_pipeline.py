"""Chạy pipeline dự báo ShelfSense trên GitHub Actions.

PIPELINE_MODE=train_predict: huấn luyện mô hình cuối rồi dự báo.
PIPELINE_MODE=predict_only: tải lightgbm_ton_kho.joblib, bỏ qua huấn luyện rồi dự báo.
Hai script notebook vẫn chạy chung namespace để giữ tương thích các biến/hàm hiện có.
"""
import os
import pathlib
import re

os.environ.setdefault("MPLBACKEND", "Agg")
HERE = pathlib.Path(__file__).parent
MODE = os.environ.get("PIPELINE_MODE", "train_predict").strip().lower()
if MODE not in {"train_predict", "predict_only"}:
    raise ValueError("PIPELINE_MODE phải là train_predict hoặc predict_only")
if MODE == "predict_only" and not (HERE / "lightgbm_ton_kho.joblib").exists():
    raise FileNotFoundError(
        "Thiếu notebooks/lightgbm_ton_kho.joblib. "
        "Cần chạy train_predict thành công trước khi chạy predict_only."
    )

ns = {"__name__": "__main__"}
for ten in ("file_training.py", "file_du_doan.py"):
    dong = (HERE / ten).read_text(encoding="utf-8").splitlines()
    dong = ["" if l.lstrip().startswith(("!", "%")) else l for l in dong]
    code = "\n".join(dong)
    if os.environ.get("PROPHET") != "1":
        code = re.sub(r"^CHAY_PROPHET\s*=\s*True", "CHAY_PROPHET = False", code, flags=re.M)
    print(f"===== Chạy {ten} | mode={MODE} =====", flush=True)
    exec(compile(code, str(HERE / ten), "exec"), ns)

print(f"===== Hoàn tất pipeline: {MODE}; dự báo đã được ghi lên BigQuery =====", flush=True)
