from django.urls import path
from . import views
from . import maps

app_name = "dashboard"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cartes/", maps.carte, name="carte"),
    path("cartes/data/", maps.carte_data, name="carte_data"),
    path("cartes/stats/", maps.carte_stats, name="carte_stats"),
    path("cartes/mensuel/", maps.carte_monthly, name="carte_monthly"),
    # endpoints couche 2 conservés (utilisés par l'onglet « par indicateur »)
    path("cartes/indicateur/data/", maps.carte_couche2_data, name="carte_couche2_data"),
]
