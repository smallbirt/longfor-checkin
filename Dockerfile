ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 5 --index-url "${PIP_INDEX_URL}" -r requirements.txt && useradd -u 10001 -m checkin && mkdir /data && chown checkin:checkin /data
COPY app ./app
COPY manage.py .
USER checkin
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python", "-m", "app"]
