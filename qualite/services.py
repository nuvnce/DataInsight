"""
Services d'orchestration de la couche 1
========================================

Colle les moteurs (complétude, cohérence, atypiques) aux écrans. Deux rôles :

1. **Déduire le périmètre** d'un lot : quelles unités, périodes, désagrégations
   et quels éléments sont concernés. Ici, on le déduit des `DataValue` déjà
   présentes pour l'année du lot (approche simple, cohérente avec l'existant :
   les données arrivent par les imports, le lot les fiabilise). Quand le dépôt
   de fichier arrivera, on bornera le périmètre au contenu du fichier.

2. **Lancer les trois contrôles** sur une version et poser le résultat.
"""
from __future__ import annotations

from collecte.models import DataValue
from collecte import periods as P
from . import completeness, consistency, outliers
from .models import BatchVersion, ValidationRule


def _expected_periods(year, present_periods):
    """
    Les périodes *attendues* pour l'année, pas seulement celles présentes —
    sinon la complétude ne verrait jamais un mois entièrement vide.

    On déduit le rythme (mensuel / trimestriel / annuel) des périodes déjà
    présentes, et on génère la grille complète de l'année pour ce rythme :
      - mensuel     : 202401 … 202412
      - trimestriel : 2024Q1 … 2024Q4
      - annuel      : 2024
    S'il n'y a aucune donnée (rien pour déduire le rythme), on tombe sur les 12
    mois par défaut.
    """
    y = int(year)
    # type détecté à partir d'une période présente, sinon mensuel
    ptype = P.MONTHLY
    for p in present_periods:
        try:
            ptype = P.detect_type(p)
            break
        except P.InvalidPeriod:
            continue

    if ptype == P.MONTHLY:
        return [f"{y}{m:02d}" for m in range(1, 13)]
    if ptype == P.QUARTERLY:
        return [f"{y}Q{q}" for q in range(1, 5)]
    return [str(y)]


def perimeter_for(batch):
    """
    Périmètre d'un lot pour les trois contrôles.

    - unités / éléments / désagrégations : ceux qui *rapportent* pour l'année
      (déduits des DataValue présentes — c'est le champ des entités suivies).
    - périodes : la **grille complète attendue** pour l'année (voir
      `_expected_periods`), pour que la complétude repère les périodes vides.

    Approche cohérente avec l'existant : les données arrivent par les imports,
    le lot les fiabilise. Quand le dépôt de fichier arrivera, on bornera le
    périmètre au contenu du fichier déposé.
    """
    year = str(batch.year)
    qs = DataValue.objects.filter(period__startswith=year)

    org_unit_ids = sorted(set(qs.values_list("org_unit_id", flat=True)))
    data_element_ids = sorted(set(qs.values_list("data_element_id", flat=True)))
    coc_ids = sorted(set(qs.values_list("category_option_combo_id", flat=True)))
    present_periods = sorted(set(qs.values_list("period", flat=True)))

    periods_list = _expected_periods(year, present_periods)

    return {
        "org_unit_ids": org_unit_ids,
        "periods": periods_list,
        "data_element_ids": data_element_ids,
        "coc_ids": coc_ids,
        "present_periods": present_periods,
    }


def next_version(batch, produced_by, stage):
    """Crée la version suivante d'un lot (numérotation continue)."""
    cur = batch.current_version
    number = (cur.number + 1) if cur else 1
    return BatchVersion.objects.create(
        batch=batch, number=number, stage=stage, produced_by=produced_by,
    )


def run_all_checks(version, perimeter):
    """
    Lance les trois contrôles sur une version, dans l'ordre : complétude →
    cohérence → atypiques. Renvoie les trois QualityCheck.
    """
    comp = completeness.run_and_record(
        version,
        perimeter["org_unit_ids"], perimeter["periods"],
        perimeter["data_element_ids"], perimeter["coc_ids"],
    )
    cons = consistency.run_and_record(
        version,
        perimeter["org_unit_ids"], perimeter["periods"], perimeter["coc_ids"],
        rules=ValidationRule.objects.filter(is_active=True),
    )
    outl = outliers.run_and_record(
        version,
        perimeter["org_unit_ids"], perimeter["periods"],
        perimeter["data_element_ids"], perimeter["coc_ids"],
    )
    return comp, cons, outl
