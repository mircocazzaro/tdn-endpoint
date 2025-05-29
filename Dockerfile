# 1. Base image with Python & Java & Supervisor
FROM python:3.10-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential default-jre-headless supervisor \
 && rm -rf /var/lib/apt/lists/*

# 2. Create app dir
WORKDIR /app

# 3. Install Python deps
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# 4. Copy code and Ontop files
COPY . .

# 5. Collect static assets
RUN python manage.py collectstatic --noinput

# 6. Expose the three container ports
EXPOSE 8000 8084

# 7. Add Supervisor config & launch
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
CMD ["supervisord", "-n"]
