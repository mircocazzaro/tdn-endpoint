# Dockerfile (Django + SPARQL helper)
FROM python:3.10-slim

# allow host to override the bind port (internal)
ENV DJANGO_PORT=8000

WORKDIR /app

# install OS deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
       build-essential default-jre-headless \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# copy source
COPY . .

# collect static
RUN python manage.py collectstatic --noinput

# tell Docker which ports the container listens on
EXPOSE ${DJANGO_PORT} 8084

# launch both servers
CMD sh -c "\
    # run migrations, then start Gunicorn on configurable port
    python manage.py migrate && \
    gunicorn myproject.wsgi:application --bind 0.0.0.0:${DJANGO_PORT} & \
    # note: the SPARQL Helper thread in apps.py always listens on 8084 internally
    wait \
"
