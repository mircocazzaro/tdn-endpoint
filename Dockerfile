FROM python:3.10-slim

WORKDIR /app

# OS deps (incl. Java for your helper thread, if needed)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential default-jre-headless \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# App code + static
COPY . .
RUN python manage.py collectstatic --noinput

# Exposed container ports
EXPOSE 8000 8084

# Run migrations, then launch both:
CMD sh -c "\
    python manage.py migrate && \
    gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 & \
    wait \
"
