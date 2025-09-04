FROM python:3.11-slim

WORKDIR /Mafia
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install -r requirements.txt
COPY . .

CMD [ "python", "main.py" ]
