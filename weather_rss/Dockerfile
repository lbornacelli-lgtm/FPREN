FROM python:3.12-slim

# Set workdir
WORKDIR /app

# Copy code
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Default command (can override in docker-compose)
CMD ["python", "weather_service.py"]
