# Enterprise Production Dockerfile for AI Data Analyst Pro
FROM python:3.12-slim

# Install system dependencies & Microsoft SQL Server ODBC Driver 17
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    unixodbc-dev \
    libgomp1 \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY . .

# Expose HTTP port
EXPOSE 5000

# Environment defaults
ENV PORT=5000
ENV PYTHONUNBUFFERED=1

# Production entrypoint
CMD ["python", "wsgi.py"]
