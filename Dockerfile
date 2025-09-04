# از نسخه کامل Python 3.11 استفاده می‌کنیم (نه slim)
FROM python:3.11

# محل کار
WORKDIR /Mafia

# ساخت venv و اضافه کردنش به PATH
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# نصب ابزارهای پایه (داخل ایمیج کامل معمولاً هست ولی برای اطمینان اضافه می‌کنیم)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

# ارتقا pip و ابزارهای build
RUN python -m pip install --upgrade pip setuptools wheel

# نصب وابستگی‌ها
COPY requirements.txt .
RUN python -m pip install -r requirements.txt

# کپی سورس پروژه
COPY . .

# اجرای برنامه
CMD ["python", "main.py"]
