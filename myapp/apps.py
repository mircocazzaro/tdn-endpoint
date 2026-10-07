from django.apps import AppConfig


class MyappConfig(AppConfig):
    """Configurazione dell'applicazione.

    ready() non avvia processi ne' server: veniva eseguito anche da migrate,
    collectstatic, shell e dai test. Il servizio SPARQL protetto e' avviato
    soltanto da run_sparql_server.py.
    """

    name = 'myapp'
