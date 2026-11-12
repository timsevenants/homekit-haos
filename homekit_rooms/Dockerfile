FROM python:3.12-alpine

RUN pip install --no-cache-dir aiohttp==3.10.10

WORKDIR /app
COPY server.py /app/
COPY static /app/static

CMD ["python3", "-u", "/app/server.py"]
