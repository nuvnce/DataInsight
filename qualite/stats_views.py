"""
stats_views.py — Couche 2 : tableau de bord récap par district
================================================================

La page d'analyse validée par le chef : un sélecteur de période, puis un
tableau avec une ligne par district (familles consolidées + les trois
indicateurs). S'appuie entièrement sur `consolidation.district_table`.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from metadata.models import OrgUnit
from collecte.models import DataValue
from . import consolidation as K


def _available_periods():
    """Les périodes présentes en base (toutes), de la plus récente à la plus ancienne."""
    return sorted(set(DataValue.objects.values_list("period", flat=True)), reverse=True)


@login_required
def recap(request):
    """Tableau de bord récapitulatif par district pour une période."""
    periods = _available_periods()
    sel = request.GET.get("period") or (periods[0] if periods else None)
    secteur = request.GET.get("secteur") or None   # None=consolidé, PUB, PRV

    rows = []
    totals = {code: 0 for code in K.FAMILIES}
    if sel:
        districts = OrgUnit.objects.filter(level=3).order_by("name")
        rows = K.district_table(sel, districts=districts, secteur=secteur)
        # ligne de total (national) : somme des familles
        for r in rows:
            for code in K.FAMILIES:
                totals[code] += r.values.get(code, 0)

    return render(request, "qualite/recap.html", {
        "periods": periods,
        "selected_period": sel,
        "secteur": secteur or "",
        "rows": rows,
        "families": K.FAMILIES,
        "totals": totals,
        "has_data": bool(rows),
        "active": "stats",
    })
