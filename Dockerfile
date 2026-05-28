FROM python:3.10-slim

WORKDIR /app

# Create directory for persistent Railway Volume mounting
RUN mkdir -p /data/sessions /data/configs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
