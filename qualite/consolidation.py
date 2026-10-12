"""
consolidation.py — Couche 2 : consolidation et indicateurs par district
=========================================================================

La couche 2 part des données fiabilisées (DataValue) et produit la vue
d'analyse : un tableau par district × période, avec les familles consolidées
et les trois indicateurs validés par le chef.

Consolidation public / privé
-----------------------------
Les données du modèle sont agrégées au district (milieu « AGG »), avec le
secteur en désagrégation (PUB / PRV). Consolider = **sommer PUB + PRV**. On peut
aussi garder le détail par secteur si besoin.

Double comptage
---------------
On réutilise le filtrage d'âge de `collecte.indicators` : on somme TOUJOURS sur
une seule tranche (TOT par défaut), jamais TOT + détail en même temps.

Les trois indicateurs
---------------------
  Létalité            = DECES / CONF_TOT × 100
  Taux de positivité  = TDR_POS / TDR_FAIT × 100
  Incidence (‰)       = CONF_TOT / population × 1000   (population de l'année)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from metadata.models import OrgUnit, DataElement
from collecte.models import DataValue, Population


AGE_TOTAL = "TOT"

# Familles affichées dans le tableau récap (codes du modèle).
FAMILIES = ["SUS", "SUS_TESTE", "TDR_FAIT", "TDR_POS",
            "CONF_TOT", "CONF_SIMPLE", "CONF_GRAVE", "GRAVE_REF",
            "TRAITE_CTA", "HOSP", "DECES"]


def _sum(element_code, district, period, age=AGE_TOTAL, secteur=None) -> Decimal:
    """
    Somme d'un élément pour un district (sous-arbre) et une période, filtrée sur
    une tranche d'âge. `secteur` None = PUB + PRV consolidés ; sinon restreint.
    """
    de = DataElement.objects.filter(code=element_code).first()
    if de is None:
        return Decimal(0)
    qs = DataValue.objects.filter(data_element=de, period=period)
    if district is not None:
        qs = qs.filter(org_unit__path__startswith=district.path)
    if age is not None:
        qs = qs.filter(
            category_option_combo__options__category__code="AGE",
            category_option_combo__options__code=age,
        )
    if secteur is not None:
        qs = qs.filter(
            category_option_combo__options__category__code="SECTEUR",
            category_option_combo__options__code=secteur,
        )
    return qs.aggregate(s=Sum("value"))["s"] or Decimal(0)


def _population(district, year, age=None) -> Decimal:
    """Population du district pour l'année (tous âges par défaut)."""
    qs = Population.objects.filter(org_unit=district, year=year)
    if age is None:
        qs = qs.filter(age_group__isnull=True, sex__isnull=True)
    else:
        qs = qs.filter(age_group=age)
    return Decimal(qs.aggregate(s=Sum("value"))["s"] or 0)


def _ratio(num: Decimal, den: Decimal, factor: int):
    """Ratio sûr : None si dénominateur nul."""
    if den == 0:
        return None
    return (num / den * factor).quantize(Decimal("0.01"))


@dataclass
class DistrictRow:
    district_id: int
    district_name: str
    period: str
    values: dict          # code -> Decimal (familles consolidées)
    letalite: Decimal | None
    positivite: Decimal | None
    incidence: Decimal | None


def _year_of(period: str) -> int:
    """Année portée par un code période (AAAAMM, AAAAQx, AAAA)."""
    return int(str(period)[:4])


def district_table(period, districts=None, age=AGE_TOTAL, secteur=None):
    """
    Tableau de la couche 2 pour une période : une ligne par district, familles
    consolidées + les trois indicateurs.

    districts : itérable d'OrgUnit (niveau district) ; défaut = tous les niveau 3.
    secteur   : None = consolidé PUB+PRV ; "PUB"/"PRV" pour un seul secteur.
    """
    if districts is None:
        districts = OrgUnit.objects.filter(level=3).order_by("name")
    year = _year_of(period)

    rows = []
    for d in districts:
        vals = {code: _sum(code, d, period, age, secteur) for code in FAMILIES}
        pop = _population(d, year)
        letal = _ratio(vals["DECES"], vals["CONF_TOT"], 100)
        posit = _ratio(vals["TDR_POS"], vals["TDR_FAIT"], 100)
        incid = _ratio(vals["CONF_TOT"], pop, 1000) if pop else None
        rows.append(DistrictRow(
            district_id=d.id, district_name=d.name, period=period,
            values=vals, letalite=letal, positivite=posit, incidence=incid,
        ))
    return rows


def sector_split(element_code, district, period, age=AGE_TOTAL) -> dict:
    """Détail public / privé d'un élément (pour vérifier la consolidation)."""
    return {
        "PUB": _sum(element_code, district, period, age, secteur="PUB"),
        "PRV": _sum(element_code, district, period, age, secteur="PRV"),
        "TOTAL": _sum(element_code, district, period, age, secteur=None),
    }


# ---------------------------------------------------------------------------
#  Complétude par district × période (pour la grille de cartes mensuelles)
# ---------------------------------------------------------------------------
# Les familles « cœur » attendues pour juger la complétude du reporting d'un
# district. On ne compte que le total (TOT) et le secteur public par défaut, et
# on regarde si une valeur existe pour chaque (famille) à la période donnée.
CORE_FAMILIES = ["SUS", "TDR_FAIT", "TDR_POS", "CONF_TOT", "TRAITE_CTA"]


def completeness_by_district(period, districts=None, families=None,
                             age=AGE_TOTAL):
    """
    Taux de complétude du reporting par district pour une période : part des
    familles « cœur » qui ont au moins une valeur rapportée.

    Renvoie une liste de dicts {district_id, district_name, score, reported,
    expected}. score en %, None si expected = 0.
    """
    from metadata.models import OrgUnit as _OU
    districts = districts if districts is not None else _OU.objects.filter(level=3).order_by("name")
    families = families or CORE_FAMILIES

    out = []
    for d in districts:
        reported = 0
        for code in families:
            if _sum(code, d, period, age) > 0:
                reported += 1
        expected = len(families)
        score = None if expected == 0 else round(reported / expected * 100, 1)
        out.append({
            "district_id": d.id, "district_name": d.name,
            "score": score, "reported": reported, "expected": expected,
        })
    return out


def national_completeness(period, districts=None, families=None, age=AGE_TOTAL):
    """% national = moyenne des scores district (districts sans attendu ignorés)."""
    rows = completeness_by_district(period, districts, families, age)
    scores = [r["score"] for r in rows if r["score"] is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)
