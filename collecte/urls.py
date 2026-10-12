from django.urls import path

from . import views

app_name = "collecte"

urlpatterns = [
    path("", views.import_home, name="import_home"),
    path("saisie/", views.data_entry, name="data_entry"),
    path("template/", views.download_template, name="download_template"),
    path("import/", views.import_excel, name="import_excel"),
    path("import-souple/", views.import_flexible, name="import_flexible"),
    path("import-dhis2/", views.import_dhis2_page, name="import_dhis2"),
    path("convertir-dhis2/", views.convert_dhis2_page, name="convert_dhis2"),
    path("import-modele/", views.import_template_page, name="import_template"),
    path("import-population/", views.import_population_page, name="import_population"),
]
