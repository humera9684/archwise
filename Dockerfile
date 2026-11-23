FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY advisor/ advisor/
COPY api/ api/
COPY pricing_etl/ pricing_etl/
COPY migrations/ migrations/
COPY config/ config/
COPY knowledge/ knowledge/
EXPOSE 8000
# Default: the API. docker-compose overrides the command for the ETL.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
