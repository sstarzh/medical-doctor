FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir . && useradd -m app && mkdir -p /data && chown app /data
USER app
ENV MEDICAL_DOCTOR_TRANSPORT=http HOST=0.0.0.0 PORT=8000 MEDICAL_DOCTOR_DB=/data/auth.db
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["medical-doctor-mcp"]
