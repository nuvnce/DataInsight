from django.urls import path

from . import views
from . import stats_views

app_name = "qualite"

urlpatterns = [
    path("stats/", stats_views.recap, name="recap"),
    path("", views.batch_list, name="batch_list"),
    path("nouveau/", views.batch_create, name="batch_create"),
    path("lot/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("lot/<int:pk>/controles/", views.run_checks, name="run_checks"),
    path("version/<int:pk>/anomalies/", views.version_anomalies, name="version_anomalies"),
    path("version/<int:pk>/signer/", views.sign_version, name="sign_version"),
    path("anomalie/<int:pk>/decision/", views.decide_anomaly, name="decide_anomaly"),
]
