from django.urls import path
from . import views
from . import maps

app_name = "dashboard"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cartes/", maps.carte, name="carte"),
    path("cartes/data/", maps.carte_data, name="carte_data"),
]
