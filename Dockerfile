# HDN Endpoint: interfaccia di amministrazione (Django, porta 8000),
# servizio SPARQL protetto per HDN Central (porta 8084) e Ontop.

FROM python:3.10-slim

# Java per Ontop, supervisor per i processi. Nessun compilatore: tutte le
# dipendenze Python hanno wheel binarie per questa piattaforma, e
# --only-binary=:all: fa fallire il build invece di ripiegare in silenzio su
# una compilazione dai sorgenti, che richiederebbe una toolchain C.
RUN apt-get update \
 && apt-get install -y --no-install-recommends default-jre-headless supervisor \
 && rm -rf /var/lib/apt/lists/*

# Utente senza privilegi: i processi dell'endpoint non girano come root.
RUN useradd --create-home --uid 10001 hdn

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY --chown=hdn:hdn . .
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

USER hdn

# uploads/ ospita i CSV caricati e lo stato locale: non e' nel contesto di
# build (.dockerignore) e va creata vuota.
RUN mkdir -p uploads \
 && python manage.py collectstatic --noinput

EXPOSE 8000 8084

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ontop/status/', timeout=4).status == 200 else 1)"

CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
