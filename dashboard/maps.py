"""
maps.py — Vues de la cartographie (brique 4b)
==============================================

Deux vues :
  - carte(request)      : la page HTML avec Leaflet (choix indicateur/période).
  - carte_data(request) : renvoie un GeoJSON des districts + une valeur par
                          district (volume brut OU indicateur calculé), que la
                          carte colore en dégradé (choroplèthe).

Chemin A : le GeoJSON des frontières vient du champ texte OrgUnit.geometry.
Aucun PostGIS.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render

from metadata.models import OrgUnit, DataElement
from collecte.models import DataValue, Indicator
from collecte import periods
from collecte.indicators import compute_indicator


def _districts_with_geometry():
    """Districts (niveau 3) qui ont une frontière chargée."""
    return OrgUnit.objects.filter(level=3).exclude(geometry__isnull=True).exclude(geometry="")


@login_required
def carte(request):
    """Page de cartographie."""
    context = {
        "elements": DataElement.objects.order_by("name"),
        "indicators": Indicator.objects.order_by("name"),
        "active": "cartes",
    }
    return render(request, "dashboard/carte.html", context)


@login_required
def carte_data(request):
    """
    Renvoie un GeoJSON FeatureCollection : chaque district avec sa géométrie et
    une propriété `value` (nombre) à colorer.

    Paramètres GET :
      mode=volume  & element=<code>   -> somme des valeurs de l'élément (TOT)
      mode=indicator & indicator=<code> -> valeur de l'indicateur calculé
      period=<code>  (optionnel)      -> restreint à une période, sinon toutes
    """
    mode = request.GET.get("mode", "volume")
    period = request.GET.get("period") or None

    districts = _districts_with_geometry()
    features = []
    values_for_scale = []

    if mode == "indicator":
        indicator = Indicator.objects.filter(code=request.GET.get("indicator")).first()
        for ou in districts:
            value = None
            if indicator is not None:
                r = compute_indicator(indicator, org_unit=ou, period=period)
                value = float(r["value"]) if r["value"] is not None else None
            features.append(_feature(ou, value))
            if value is not None:
                values_for_scale.append(value)
        label = indicator.name if indicator else "—"
        unit = "%" if indicator and float(indicator.factor) == 100 else ""
    else:
        element = DataElement.objects.filter(code=request.GET.get("element")).first()
        for ou in districts:
            value = None
            if element is not None:
                qs = DataValue.objects.filter(
                    data_element=element,
                    org_unit__path__startswith=ou.path,
                    # ne compter que les totaux d'âge (évite le double comptage)
                    category_option_combo__options__category__code="AGE",
                    category_option_combo__options__code="TOT",
                )
                if period:
                    qs = qs.filter(period=period)
                total = qs.aggregate(s=Sum("value"))["s"]
                value = float(total) if total is not None else None
            features.append(_feature(ou, value))
            if value is not None:
                values_for_scale.append(value)
        label = element.name if element else "—"
        unit = ""

    vmin = min(values_for_scale) if values_for_scale else 0
    vmax = max(values_for_scale) if values_for_scale else 0

    return JsonResponse({
        "type": "FeatureCollection",
        "features": features,
        "meta": {"label": label, "unit": unit, "vmin": vmin, "vmax": vmax,
                 "period": periods.label(period) if period else "Toutes périodes"},
    })


def _feature(ou, value):
    """Construit une Feature GeoJSON pour un district."""
    try:
        geom = json.loads(ou.geometry)
    except (TypeError, ValueError):
        geom = None
    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {"name": ou.name, "value": value},
    }
