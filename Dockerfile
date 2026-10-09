FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir .
RUN useradd -m app
USER app
EXPOSE 8000
CMD ["medical-doctor-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
