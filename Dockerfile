FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY gemini_web2api.py account_manager.py db.py metrics.py dashboard.py ./
COPY web/ ./web/
COPY config.example.json ./config.json
EXPOSE 8081

# Mount your own cookies/ and config.json over this image's defaults at runtime, e.g.:
#   docker run -p 8081:8081 -v $(pwd)/cookies:/app/cookies -v $(pwd)/config.json:/app/config.json 0hive
CMD ["python", "gemini_web2api.py", "--config", "/app/config.json"]
