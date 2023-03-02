FROM python:3.11

WORKDIR /app

COPY requirements.freeze.txt ./
RUN pip install --no-cache-dir -r requirements.freeze.txt

COPY templates templates
COPY main.py update.py ./

ENV PYTHONUNBUFFERED 1

CMD ["python", "main.py", "--ip", "0.0.0.0", "--port", "8080"]
