"""Chạy file_training.py rồi file_du_doan.py trong CÙNG một namespace
(giống hai nhóm ô nằm trong cùng một phiên Colab), dùng được trên GitHub Actions.

- Bỏ các dòng '!pip ...' / '%magic' của Colab (Python thường không hiểu).
- Tắt Prophet khi chạy tự động (nặng, cần cmdstan). Bật lại bằng biến môi trường PROPHET=1.
"""
import os
import pathlib
import re

os.environ.setdefault("MPLBACKEND", "Agg")  # không có màn hình -> vẽ biểu đồ không mở cửa sổ

HERE = pathlib.Path(__file__).parent
ns = {"__name__": "__main__"}

for ten in ("file_training.py", "file_du_doan.py"):
    dong = (HERE / ten).read_text(encoding="utf-8").splitlines()
    dong = ["" if l.lstrip().startswith(("!", "%")) else l for l in dong]  # giữ nguyên số dòng
    code = "\n".join(dong)
    if os.environ.get("PROPHET") != "1":
        code = re.sub(r"^CHAY_PROPHET\s*=\s*True", "CHAY_PROPHET = False", code, flags=re.M)
    print(f"===== Chạy {ten} =====", flush=True)
    exec(compile(code, str(HERE / ten), "exec"), ns)

print("===== Xong: đã huấn luyện và ghi dự báo lên BigQuery =====")
