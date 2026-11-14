"""Utente di default per l'interfaccia di amministrazione.

Viene creato solo se il database non ha ancora nessun utente: un'installazione
che ha gia' i suoi account non viene toccata. La password di default e' nota a
chiunque legga il repository: l'interfaccia mostra un avviso finche' non viene
cambiata (myapp/accounts.py).
"""
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "hereditary"


def create_default_admin(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    if User.objects.exists():
        return
    User.objects.create(username=DEFAULT_USERNAME, password=make_password(DEFAULT_PASSWORD),
                        is_staff=True)


class Migration(migrations.Migration):

    dependencies = [
        ('myapp', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_default_admin, migrations.RunPython.noop),
    ]
