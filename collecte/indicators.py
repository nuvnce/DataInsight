"""
indicators.py — Moteur de calcul des indicateurs dérivés (corrigé — brique 2)
=============================================================================

Un indicateur dérivé = numérateur / dénominateur × facteur.

CORRECTIF DOUBLE COMPTAGE
-------------------------
Le fichier DHIS2 contient, pour un même indicateur, le TOTAL tous âges (TOT)
ET son détail par tranche (U5, O5, FE) en même temps. Sommer sans distinguer
revient à compter TOT + U5 + O5 + FE ≈ 2× la vraie valeur → positivité > 100 %.

La somme est donc désormais TOUJOURS filtrée sur une tranche d'âge :
  - age_group="TOT" (défaut) : ne compte que les valeurs totales (recommandé)
  - age_group="U5"/"O5"/"FE" : ne compte que cette tranche (indicateur par âge)
  - age_group=None : somme TOUT (ancien comportement — à éviter, laissé pour
    compat/débogage ; c'est lui qui causait le bug).

On peut aussi filtrer par milieu et secteur si besoin (mêmes options de combo).
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from metadata.models import DataElement
from .models import DataValue, Indicator


# Codes des options d'âge (catégorie AGE dans seed_dimensions)
AGE_TOTAL = "TOT"


def _sum_for(element_code, org_unit=None, period=None,
             age_group=AGE_TOTAL, milieu=None, secteur=None) -> Decimal:
    """
    Somme des valeurs d'un élément, filtrée par unité (sous-arbre), période, et
    surtout par tranche d'âge (pour éviter le double comptage TOT + détail).

    age_group : "TOT"/"U5"/"O5"/"FE" pour ne sommer QUE cette tranche,
                ou None pour tout sommer (déconseillé — mélange les granularités).
    milieu / secteur : codes optionnels ("COMM"/"FS", "PUB"/"PRV") pour restreindre.
    """
    de = DataElement.objects.filter(code=element_code).first()
    if de is None:
        return Decimal(0)

    qs = DataValue.objects.filter(data_element=de)
    if org_unit is not None:
        qs = qs.filter(org_unit__path__startswith=org_unit.path)
    if period is not None:
        qs = qs.filter(period=period)

    # Filtre sur l'option d'âge portée par le combo de désagrégation.
    # C'est LE correctif : on ne mélange plus total et détail.
    if age_group is not None:
        qs = qs.filter(
            category_option_combo__options__category__code="AGE",
            category_option_combo__options__code=age_group,
        )
    if milieu is not None:
        qs = qs.filter(
            category_option_combo__options__category__code="MILIEU",
            category_option_combo__options__code=milieu,
        )
    if secteur is not None:
        qs = qs.filter(
            category_option_combo__options__category__code="SECTEUR",
            category_option_combo__options__code=secteur,
        )

    total = qs.aggregate(s=Sum("value"))["s"]
    return total or Decimal(0)


def compute_indicator(indicator: Indicator, org_unit=None, period=None,
                      age_group=AGE_TOTAL, milieu=None, secteur=None) -> dict:
    """
    Calcule un indicateur. Retourne {numerator, denominator, value, factor}.
    value = None si dénominateur nul (évite la division par zéro).

    Par défaut, numérateur et dénominateur sont calculés sur la MÊME tranche
    d'âge (TOT), ce qui garantit un ratio cohérent (ex. positivité ≤ 100 %).
    """
    num = _sum_for(indicator.numerator_code, org_unit, period, age_group, milieu, secteur)
    den = _sum_for(indicator.denominator_code, org_unit, period, age_group, milieu, secteur)
    value = None if den == 0 else (num / den) * indicator.factor
    return {
        "numerator": num, "denominator": den,
        "value": value, "factor": indicator.factor,
    }


def compute_series(indicator: Indicator, org_unit=None, periods=None,
                   age_group=AGE_TOTAL, milieu=None, secteur=None) -> list[dict]:
    """
    Série temporelle de l'indicateur. Même filtrage d'âge que compute_indicator.
    Si periods=None, prend toutes les périodes présentes pour le numérateur.
    """
    if periods is None:
        de = DataElement.objects.filter(code=indicator.numerator_code).first()
        if de is None:
            return []
        qs = DataValue.objects.filter(data_element=de)
        if org_unit is not None:
            qs = qs.filter(org_unit__path__startswith=org_unit.path)
        periods = sorted(set(qs.values_list("period", flat=True)))

    out = []
    for p in periods:
        r = compute_indicator(indicator, org_unit, p, age_group, milieu, secteur)
        out.append({"period": p, "value": r["value"],
                    "numerator": r["numerator"], "denominator": r["denominator"]})
    return out
