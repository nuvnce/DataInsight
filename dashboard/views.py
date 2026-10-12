"""
Tableau de bord — avec sélecteur de dashboards thématiques (brique 5c).

Chaque « dashboard thématique » est une vue préparée qui applique un filtre de
milieu au même calcul de fond :
  - vue-ensemble       : aucun filtre (tous milieux)
  - communautaire      : milieu = ASC (communautaire)
  - formation-sanitaire: milieu = formation sanitaire

Pour ajouter un thème plus tard, il suffit d'ajouter une entrée dans DASHBOARDS
(clé, libellé, code de milieu ou None). Le reste est automatique.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from metadata.models import OrgUnit, DataElement
from collecte.models import DataValue
from collecte import periods


# Dashboards thématiques disponibles.
# milieu = code de l'option de milieu à filtrer, ou None pour « tous milieux ».
# Les codes de milieu viennent de seed_dimensions (catégorie MILIEU).
DASHBOARDS = [
    {"key": "vue-ensemble",        "label": "Vue d'ensemble",       "milieu": None},
    {"key": "communautaire",       "label": "Communautaire (ASC)",  "milieu": "COMM"},
    {"key": "formation-sanitaire", "label": "Formation sanitaire",  "milieu": "FS"},
]

def _dashboard_by_key(key):
    for d in DASHBOARDS:
        if d["key"] == key:
            return d
    return DASHBOARDS[0]  # défaut : vue d'ensemble


@login_required
def dashboard(request):
    elements = DataElement.objects.order_by("name")
    org_units = OrgUnit.objects.order_by("level", "name")

    de_code = request.GET.get("element") or ""
    ou_id = request.GET.get("org_unit") or ""
    dash_key = request.GET.get("dashboard") or DASHBOARDS[0]["key"]
    current_dashboard = _dashboard_by_key(dash_key)

    selected_de = DataElement.objects.filter(code=de_code).first()
    if selected_de is None:
        selected_de = elements.first()
    selected_ou = OrgUnit.objects.filter(pk=ou_id).first() if ou_id else None

    context = {
        "elements": elements,
        "org_units": org_units,
        "selected_de": selected_de,
        "selected_ou": selected_ou,
        "dashboards": DASHBOARDS,
        "current_dashboard": current_dashboard,
        "has_data": False,
    }
    if selected_de is None:
        return render(request, "dashboard/dashboard.html", context)

    # Périmètre d'unités : le sous-arbre de l'unité choisie, sinon tout
    values = DataValue.objects.filter(data_element=selected_de)
    if selected_ou:
        values = values.filter(org_unit__path__startswith=selected_ou.path)

    # Filtre de dashboard thématique : restreindre au milieu du thème
    if current_dashboard["milieu"] is not None:
        values = values.filter(
            category_option_combo__options__category__code="MILIEU",
            category_option_combo__options__code=current_dashboard["milieu"],
        )

    # Feuilles concernées (unités qui portent réellement des valeurs)
    leaf_units = (
        OrgUnit.objects.filter(pk__in=values.values_list("org_unit_id", flat=True))
        .order_by("name")
    )

    all_periods = sorted(set(values.values_list("period", flat=True)))

    # Tableau croisé unité × période (somme sur les désagrégations restantes)
    grid = {}
    for v in values.values("org_unit_id", "period").annotate(total=Sum("value")):
        grid[(v["org_unit_id"], v["period"])] = v["total"]

    table_rows = []
    for ou in leaf_units:
        cells = [grid.get((ou.id, p)) for p in all_periods]
        table_rows.append({"org_unit": ou, "cells": cells})

    series_by_period = {p: 0 for p in all_periods}
    for (ou_id_, p), total in grid.items():
        series_by_period[p] += float(total or 0)
    chart_labels = [periods.label(p) for p in all_periods]
    chart_values = [series_by_period[p] for p in all_periods]

    totals_by_unit = (
        values.values("org_unit__name").annotate(total=Sum("value")).order_by("-total")
    )
    bar_labels = [r["org_unit__name"] for r in totals_by_unit]
    bar_values = [float(r["total"] or 0) for r in totals_by_unit]

    context.update({
        "has_data": bool(all_periods),
        "periods": all_periods,
        "period_labels": [periods.label(p) for p in all_periods],
        "table_rows": table_rows,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_values_json": json.dumps(chart_values),
        "bar_labels_json": json.dumps(bar_labels),
        "bar_values_json": json.dumps(bar_values),
        "grand_total": sum(chart_values),
    })
    return render(request, "dashboard/dashboard.html", context)
