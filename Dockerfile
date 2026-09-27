# Enterprise Production Dockerfile for AI Data Analyst Pro
# Pinned to a specific Debian release (bookworm/12), not the floating "slim" tag - that
# tag silently moves to whatever Debian release is current whenever this image is
# rebuilt, and the newest one (trixie/13) already dropped the apt-key command this
# file used to rely on, breaking the build with no code change on our side. Pinning
# keeps this build reproducible and matches a release Microsoft's ODBC driver repo
# actually publishes a package list for.
FROM python:3.12-slim-bookworm

# Install system dependencies & Microsoft SQL Server ODBC Driver 17
# apt-key is deprecated/removed on modern Debian - importing the signing key into its
# own keyring file and referencing it via signed-by= in the repo list is the current
# supported approach.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    unixodbc-dev \
    libgomp1 \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
        | sed 's/\[arch=amd64\]/[arch=amd64 signed-by=\/usr\/share\/keyrings\/microsoft-prod.gpg]/g' \
        > /etc/apt/sources.list.d/mssql-release.list \
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
