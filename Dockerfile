FROM python:3.12-slim
WORKDIR /app
COPY news.py template.html ./
ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data
CMD ["python", "news.py", "--serve"]
