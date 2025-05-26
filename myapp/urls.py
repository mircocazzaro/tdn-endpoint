# myapp/urls.py

from django.urls import path
from .views import (
    home_view, upload_csv_view, query_view,
    field_mapping_view, get_columns,
    ontop_control_view, sparql_query_view,  # ← import the new view
    ontop_status, ontop_logs, set_level, protected_sparql, delete_table_view
)

urlpatterns = [
    path('', home_view, name='home'),
    path('upload-csv/', upload_csv_view, name='upload_csv'),
    path('query/', query_view, name='query'),
    path('map-fields/', field_mapping_view, name='map_fields'),
    path('ontop-control/',    ontop_control_view, name='ontop_control'),
    path('ontop/status/',     ontop_status,       name='ontop_status'),
    path('ontop/logs/',       ontop_logs,         name='ontop_logs'),
    path('sparql/', sparql_query_view, name='sparql_query'),    # ← new
    path('get-columns/', get_columns, name='get_columns'),
    path('set-level/', set_level, name='set_level'),
    path('sparql-protected/', protected_sparql, name='sparql_protected'),
    path('delete-table/<str:table_name>/', delete_table_view, name='delete_table'),
]