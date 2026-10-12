"""
population_service.py — Intégration Django de l'import population
==================================================================

À placer dans : collecte/population_service.py

Fait le pont entre le parseur autonome (population_import.py) et la base :
mappe chaque district du fichier vers un OrgUnit de niveau 3 (par nom
normalisé), puis crée/met à jour les lignes Population (district × année).

Idempotent : la contrainte d'unicité (org_unit, year, sex, age_group) +
update_or_create garantissent qu'un réimport ne crée pas de doublon.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from metadata.models import OrgUnit
from .models import Population
from .population_import import parse_communes_sheet, normalize


@dataclass
class PopImportReport:
    parsed_rows: int = 0
    matched_districts: int = 0
    saved: int = 0
    unmatched_districts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


def _district_index() -> dict:
    """
    Index {nom_normalisé -> OrgUnit} des districts (niveau 3).
    Construit une seule fois par import.
    """
    idx = {}
    for ou in OrgUnit.objects.filter(level=3):
        idx[normalize(ou.name)] = ou
    return idx


@transaction.atomic
def import_population(path_or_buffer, save=True, source="RGPH5 projection") -> PopImportReport:
    """
    Importe la population (feuille communes agrégée au district).

    save=False -> dry-run : parse et mappe, n'écrit rien.
    """
    report = PopImportReport()

    parsed = parse_communes_sheet(path_or_buffer)
    report.parsed_rows = len(parsed.rows)
    report.warnings.extend(parsed.warnings)

    idx = _district_index()
    matched = set()
    unmatched = set()

    for row in parsed.rows:
        ou = idx.get(row.district)
        if ou is None:
            unmatched.add(row.district)
            continue
        matched.add(row.district)
        if save:
            Population.objects.update_or_create(
                org_unit=ou, year=row.year, sex=None, age_group=None,
                defaults={"value": row.value, "source": source},
            )
            report.saved += 1

    report.matched_districts = len(matched)
    report.unmatched_districts = sorted(unmatched)
    return report
