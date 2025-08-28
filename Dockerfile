# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /code

# Install system deps
RUN apt-get update && apt-get install -y \
	build-essential \
	libffi-dev \
	curl \
	&& rm -rf /var/lib/apt/lists/*

# Ensure a writable data directory
RUN mkdir -p /data

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY ./app ./app

# Set environment (optional, but helps Uvicorn)
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/code

# Run with Uvicorn for dev mode
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


