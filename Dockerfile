FROM python:3.11-slim

WORKDIR /app

# اول فقط requirements.txt برای کش بهتر
COPY requirements.txt .

# نصب پکیج‌ها
RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

# حالا کل سورس پروژه
COPY . .

# دستور اجرا
CMD ["/opt/venv/bin/python", "main.py"]
