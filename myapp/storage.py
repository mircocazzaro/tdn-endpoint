"""Statici con il prefisso della richiesta in corso (vedi myproject/prefix.py)."""
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.urls import get_script_prefix
from django.utils.encoding import filepath_to_uri


class PrefixedStaticStorage(StaticFilesStorage):
    """URL dei file statici sotto il prefisso di script corrente.

    Lo stesso prefisso di reverse(): assoluto ("/api1/") se il proxy non toglie nulla,
    relativo alla pagina ("../") se lo toglie, "/" senza proxy.
    """

    @property
    def base_url(self):
        return get_script_prefix() + "static/"

    @base_url.setter
    def base_url(self, value):
        pass

    def url(self, name):
        # concatenazione, non urljoin: urljoin("../static/", x) risolverebbe il ".." e lo perderebbe
        uri = filepath_to_uri(name)
        return self.base_url + (uri.lstrip("/") if uri else "")
