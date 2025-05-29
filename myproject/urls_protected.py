# myproject/urls_protected.py
from django.urls import path
from myproject.urls import urlpatterns as _full  # if you want to import other views
from myapp.views import sparql_protected_view  # adjust to your view

urlpatterns = [
    path("sparql-protected", sparql_protected_view, name="sparql_protected"),
    # any other truly shared hooks (like healthchecks) if needed
]
