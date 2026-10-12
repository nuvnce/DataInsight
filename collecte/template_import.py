"""
template_import.py — Import d'un modèle de collecte rempli vers la base
========================================================================

Lit un classeur au format du modèle (onglets Public / Privé ; une ligne par
district × période ; colonnes élément × âge) et écrit les `DataValue`.

Les données du modèle sont **agrégées au district**, toutes sources confondues :
le milieu utilisé est donc « AGG » (agrégé district). Le secteur vient de
l'onglet, l'âge de la colonne.

Idempotent : (élément, district, période, désagrégation) est unique ; réimporter
le même fichier met à jour au lieu de dupliquer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.db import transaction

from metadata.models import (
    OrgUnit, DataElement, CategoryOption, CategoryCombo, CategoryOptionCombo,
)
from .models import DataValue

ELEMENTS = ["SUS", "SUS_TESTE", "TDR_FAIT", "TDR_POS", "CONF_TOT",
            "CONF_SIMPLE", "CONF_GRAVE", "GRAVE_REF", "TRAITE_CTA", "HOSP", "DECES"]
AGES = ["TOT", "U5", "O5", "FE"]
SHEET_SECTOR = {"Public": "PUB", "Privé": "PRV", "Prive": "PRV"}
MILIEU_CODE = "AGG"   # données agrégées au district

_ELEMENT_LABELS = {
    "SUS": "Cas suspects de paludisme", "SUS_TESTE": "Cas suspects testés au TDR",
    "TDR_FAIT": "TDR effectués", "TDR_POS": "Cas positifs au TDR",
    "CONF_TOT": "Cas confirmés (simple et grave)", "CONF_SIMPLE": "Cas simples confirmés",
    "CONF_GRAVE": "Cas graves confirmés", "GRAVE_REF": "Cas graves référés",
    "TRAITE_CTA": "Cas confirmés traités au CTA", "HOSP": "Cas hospitalisés (paludisme)",
    "DECES": "Décès (toutes causes)",
}


@dataclass
class TemplateImportReport:
    sheets: list = field(default_factory=list)
    rows: int = 0
    saved: int = 0
    unknown_districts: set = field(default_factory=set)
    errors: list = field(default_factory=list)

    def as_dict(self):
        d = self.__dict__.copy()
        d["unknown_districts"] = sorted(self.unknown_districts)
        return d


def _combo():
    return CategoryCombo.objects.get(code="MILIEU_SECTEUR_AGE")


def _coc_for(milieu, secteur, age):
    """COC pour (milieu, secteur, âge) ; le crée si absent (dimensions seedées)."""
    combo = _combo()
    opt_meta = {o.id: (o.category.code, o.code)
                for o in CategoryOption.objects.select_related("category")}
    for coc in CategoryOptionCombo.objects.filter(combo=combo).prefetch_related("options"):
        trip = {"MILIEU": None, "SECTEUR": None, "AGE": None}
        for o in coc.options.all():
            cat, code = opt_meta.get(o.id, (None, None))
            if cat in trip:
                trip[cat] = code
        if (trip["MILIEU"], trip["SECTEUR"], trip["AGE"]) == (milieu, secteur, age):
            return coc
    return None


def _district_index():
    """districts par code ET par nom normalisé (niveau 3)."""
    import unicodedata
    def norm(s):
        n = unicodedata.normalize("NFD", str(s))
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.strip().upper().replace("-", " ").replace("_", " ")
    by_code, by_name = {}, {}
    for ou in OrgUnit.objects.filter(level=3):
        by_code[ou.code] = ou
        by_name[norm(ou.name)] = ou
    return by_code, by_name, norm


@transaction.atomic
def import_template(path_or_buffer, save=True) -> TemplateImportReport:
    """Importe un modèle rempli. save=False => dry-run (compte, n'écrit rien)."""
    report = TemplateImportReport()
    xls = pd.ExcelFile(path_or_buffer)

    by_code, by_name, norm = _district_index()
    # éléments (créés à la volée si besoin)
    combo = _combo()
    elements = {}
    for code in ELEMENTS:
        de, _ = DataElement.objects.get_or_create(
            code=code, defaults={"name": _ELEMENT_LABELS.get(code, code),
                                 "category_combo": combo})
        elements[code] = de

    to_write = []
    for sheet in xls.sheet_names:
        sector = SHEET_SECTOR.get(sheet)
        if not sector:
            continue
        report.sheets.append(sheet)
        # en-tête sur 2 lignes : on lit à plat puis on recompose
        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        if raw.shape[0] < 3:
            continue
        codes_row = raw.iloc[0].tolist()
        ages_row = raw.iloc[1].tolist()
        # propager le code élément (fusion de la ligne 1)
        cur = None
        col_map = {}   # index colonne -> (code, age)
        for j in range(3, len(codes_row)):
            if isinstance(codes_row[j], str) and codes_row[j].strip():
                cur = codes_row[j].strip()
            age = ages_row[j]
            if cur in ELEMENTS and age in AGES:
                col_map[j] = (cur, age)

        for i in range(2, raw.shape[0]):
            row = raw.iloc[i].tolist()
            district_name = row[0]
            district_code = row[1]
            period = row[2]
            if district_name is None or (isinstance(district_name, float) and pd.isna(district_name)):
                continue
            if period is None or (isinstance(period, float) and pd.isna(period)):
                continue
            period = str(period).strip()
            # ignorer la ligne d'exemple / note
            if "exemple" in str(district_name).lower():
                continue

            ou = by_code.get(str(district_code).strip()) if district_code else None
            if ou is None:
                ou = by_name.get(norm(district_name))
            if ou is None:
                report.unknown_districts.add(str(district_name))
                continue

            for j, (code, age) in col_map.items():
                val = row[j] if j < len(row) else None
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                try:
                    dval = Decimal(str(val))
                except InvalidOperation:
                    continue
                coc = _coc_for(MILIEU_CODE, sector, age)
                if coc is None:
                    report.errors.append(
                        f"Désagrégation absente : {MILIEU_CODE}/{sector}/{age} "
                        f"(as-tu relancé seed_dimensions ?)")
                    continue
                report.rows += 1
                to_write.append((elements[code], ou, period, coc, dval))

    if not save:
        return report

    for de, ou, period, coc, dval in to_write:
        DataValue.objects.update_or_create(
            data_element=de, org_unit=ou, period=period, category_option_combo=coc,
            defaults={"value": dval},
        )
        report.saved += 1
    return report
