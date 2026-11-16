"""Statici con il prefisso della richiesta in corso (vedi myproject/prefix.py)."""
from django.contrib.staticfiles.storage import StaticFilesStorage

from myproject.prefix import current_prefix


class PrefixedStaticStorage(StaticFilesStorage):
    """URL dei file statici: /static/... preceduto dal prefisso pubblico, se c'e'."""

    @property
    def base_url(self):
        return current_prefix() + "/static/"

    @base_url.setter
    def base_url(self, value):
        pass
