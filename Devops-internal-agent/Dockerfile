FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt requirements-live.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-live.txt

COPY app ./app
COPY skills ./skills
COPY sample_docs ./sample_docs

RUN mkdir -p /app/data

EXPOSE 8005

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
