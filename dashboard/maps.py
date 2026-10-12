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
    """Page de cartographie unifiée (onglets internes)."""
    all_periods = sorted(set(DataValue.objects.values_list("period", flat=True)), reverse=True)
    years = sorted({p[:4] for p in all_periods if len(p) >= 4}, reverse=True)
    context = {
        "elements": DataElement.objects.order_by("name"),
        "indicators": Indicator.objects.order_by("name"),
        "all_periods": all_periods,
        "years": years,
        "active": "cartes",
    }
    return render(request, "dashboard/carte.html", context)


@login_required
def carte_stats(request):
    """Bandeau de synthèse : districts rapportant, couples district-mois, taux, plage."""
    from qualite import consolidation as K

    year = request.GET.get("year")
    periods_list = sorted(set(DataValue.objects.values_list("period", flat=True)))
    if year:
        periods_list = [p for p in periods_list if p.startswith(year)]

    n_districts = OrgUnit.objects.filter(level=3).count()
    # couples (district, mois) ayant au moins une valeur
    reported = set()
    scores = []
    for p in periods_list:
        rows = K.completeness_by_district(p)
        for r in rows:
            if r["reported"] > 0:
                reported.add((r["district_id"], p))
            if r["score"] is not None:
                scores.append(r["score"])
    overall = round(sum(scores) / len(scores), 1) if scores else None
    span = (f"{min(periods_list)} → {max(periods_list)}" if periods_list else "—")

    return JsonResponse({
        "districts": n_districts,
        "district_months": len(reported),
        "overall_rate": overall,
        "span": span,
    })


@login_required
def carte_monthly(request):
    """
    Grille mensuelle : pour une année, un GeoJSON par mois (01→12), coloré par
    complétude du district, plus le % national du mois. Les mois sans aucune
    donnée sont marqués has_data=false.
    """
    from qualite import consolidation as K

    year = request.GET.get("year")
    if not year:
        years = sorted({p[:4] for p in DataValue.objects.values_list("period", flat=True) if len(p) >= 4})
        year = years[-1] if years else None

    districts = list(_districts_with_geometry())
    present_periods = set(DataValue.objects.filter(period__startswith=str(year))
                          .values_list("period", flat=True)) if year else set()

    months = []
    for m in range(1, 13):
        per = f"{year}{m:02d}"
        has_data = per in present_periods
        feats = []
        if has_data:
            rows = {r["district_id"]: r for r in K.completeness_by_district(per, districts=districts)}
            for ou in districts:
                r = rows.get(ou.id)
                val = r["score"] if r else None
                feats.append(_feature(ou, val))
            national = K.national_completeness(per, districts=districts)
        else:
            for ou in districts:
                feats.append(_feature(ou, None))
            national = None
        months.append({
            "month": m, "period": per, "has_data": has_data,
            "national": national,
            "geojson": {"type": "FeatureCollection", "features": feats},
        })

    return JsonResponse({"year": year, "months": months,
                         "unit": "%", "label": "Complétude"})


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


# ---------------------------------------------------------------------------
#  Données des cartes par indicateur (consolidées) — utilisé par l'onglet
#  « Carte par indicateur » de la page Cartes unifiée.
# ---------------------------------------------------------------------------
@login_required
def carte_couche2_data(request):
    """
    GeoJSON des districts coloré selon le mode couche 2.

    mode=letalite | positivite | incidence  -> indicateur consolidé (consolidation)
    mode=completude                          -> % de complétude du dernier contrôle
    period=<code>                            -> période (obligatoire pour les indicateurs)
    """
    from qualite import consolidation as K
    from qualite.models import QualityCheck

    mode = request.GET.get("mode", "letalite")
    period = request.GET.get("period") or None
    districts = _districts_with_geometry()

    features, scale = [], []
    unit = {"letalite": "%", "positivite": "%", "incidence": "‰", "completude": "%"}.get(mode, "")
    label = {"letalite": "Létalité", "positivite": "Taux de positivité",
             "incidence": "Incidence", "completude": "Complétude"}.get(mode, mode)

    if mode == "completude":
        # dernière complétude connue par district (via les anomalies localisées
        # n'est pas nécessaire : on prend le score du dernier QualityCheck COMP).
        last = (QualityCheck.objects
                .filter(step=QualityCheck.Step.COMPLETENESS, score__isnull=False)
                .order_by("-created_at").first())
        score = float(last.score) if last else None
        for ou in districts:
            # score global de complétude (même valeur partout faute de score par
            # district dans le modèle actuel) — informatif.
            features.append(_feature(ou, score))
            if score is not None:
                scale.append(score)
    else:
        rows = {r.district_id: r for r in K.district_table(period)} if period else {}
        for ou in districts:
            r = rows.get(ou.id)
            value = None
            if r is not None:
                v = getattr(r, mode, None)
                value = float(v) if v is not None else None
            features.append(_feature(ou, value))
            if value is not None:
                scale.append(value)

    vmin = min(scale) if scale else 0
    vmax = max(scale) if scale else 0
    return JsonResponse({
        "type": "FeatureCollection",
        "features": features,
        "meta": {"label": label, "unit": unit, "vmin": vmin, "vmax": vmax,
                 "period": periods.label(period) if period else "—"},
    })
