"""
dhis2_import.py — Moteur d'import des extractions DHIS2 (format large)
=====================================================================

À placer dans :  collecte/dhis2_import.py

RÔLE
----
Importer un export DHIS2 « en largeur » (une ligne = période × org unit ;
une colonne = un indicateur désagrégé) vers la table data_value.

PIPELINE
--------
1. pandas lit le fichier.
2. Colonnes de métadonnées (période, org unit) identifiées par leur nom.
3. Chaque colonne d'indicateur est passée au parseur -> (indicateur, milieu,
   âge, secteur). Les colonnes non reconnues sont signalées, pas importées.
4. Pour chaque colonne reconnue : on crée/retrouve l'élément de données et la
   combinaison de désagrégation (milieu+secteur+âge).
5. On « dépivote » : chaque cellule (ligne × colonne) devient une DataValue.
6. Insertion en masse (bulk_create par lots).

DÉPENDANCE : pandas (+ openpyxl pour lire le .xlsx).

Ce module suppose que les dimensions existent déjà (python manage.py seed_dimensions).
Il crée les ÉLÉMENTS de données à la volée (le fichier guide leur création),
mais PAS les org units : une org unit inconnue est signalée (à charger avant,
ou à activer plus tard en création auto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.db import transaction

from metadata.models import (
    OrgUnit, DataElement, Category, CategoryOption,
    CategoryCombo, CategoryOptionCombo,
)
from .models import DataValue
from . import column_parser as CP


# Colonnes de métadonnées attendues dans l'export DHIS2
PERIOD_COL = "periodcode"          # ex. 201808
ORGUNIT_CODE_COL = "organisationunitcode"   # ex. TGO0306017
ORGUNIT_NAME_COL = "organisationunitname"
META_COLS = {
    "periodid", "periodname", "periodcode", "perioddescription",
    "orgunitlevel1", "orgunitlevel2", "orgunitlevel3", "orgunitlevel4",
    "orgunitlevel5", "organisationunitid", "organisationunitname",
    "organisationunitcode", "organisationunitdescription",
}

BATCH = 5000  # taille des lots pour bulk_create


@dataclass
class ImportDHIS2Report:
    rows: int = 0
    value_cells: int = 0
    saved: int = 0
    columns_ok: int = 0
    columns_unknown: list[str] = field(default_factory=list)
    columns_ambiguous: list[str] = field(default_factory=list)
    columns_missing_dim: list[str] = field(default_factory=list)
    unknown_org_units: set = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def as_dict(self):
        d = self.__dict__.copy()
        d["unknown_org_units"] = sorted(self.unknown_org_units)
        return d


# ---------------------------------------------------------------------------
#  Résolution / création des métadonnées à partir des colonnes parsées
# ---------------------------------------------------------------------------
def _get_or_create_element(parsed) -> DataElement:
    """Crée ou retrouve l'élément de données pour un indicateur parsé."""
    combo = _milieu_secteur_age_combo()
    de, _ = DataElement.objects.get_or_create(
        code=parsed.indicator_code,
        defaults={"name": parsed.indicator_label or parsed.indicator_code,
                  "category_combo": combo},
    )
    return de


_COMBO_CACHE = {}

def _milieu_secteur_age_combo() -> CategoryCombo:
    """Retourne le combo Milieu×Secteur×Âge (créé par seed_dimensions)."""
    if "combo" not in _COMBO_CACHE:
        _COMBO_CACHE["combo"] = CategoryCombo.objects.get(code="MILIEU_SECTEUR_AGE")
    return _COMBO_CACHE["combo"]


_COC_INDEX_CACHE = {}

def _build_coc_index() -> dict:
    """
    Précharge TOUTES les combinaisons de désagrégation une seule fois et les
    indexe par triplet (milieu, secteur, âge). Il n'y en a que 16 : on évite
    ainsi une requête (et une boucle) à chaque colonne.

    Retourne { (milieu_code, secteur_code, age_code) : CategoryOptionCombo }.
    """
    if _COC_INDEX_CACHE:
        return _COC_INDEX_CACHE

    combo = _milieu_secteur_age_combo()
    # option_id -> (categorie_code, option_code), en une requête
    opt_meta = {
        o.id: (o.category.code, o.code)
        for o in CategoryOption.objects.select_related("category")
    }
    for coc in CategoryOptionCombo.objects.filter(combo=combo).prefetch_related("options"):
        triplet = {"MILIEU": None, "SECTEUR": None, "AGE": None}
        for o in coc.options.all():
            cat_code, opt_code = opt_meta.get(o.id, (None, None))
            if cat_code in triplet:
                triplet[cat_code] = opt_code
        key = (triplet["MILIEU"], triplet["SECTEUR"], triplet["AGE"])
        if None not in key:
            _COC_INDEX_CACHE[key] = coc
    return _COC_INDEX_CACHE


def _find_coc(milieu_code, secteur_code, age_code) -> CategoryOptionCombo | None:
    """Recherche O(1) dans l'index préchargé (plus aucune requête par appel)."""
    return _build_coc_index().get((milieu_code, secteur_code, age_code))


# ---------------------------------------------------------------------------
#  Import principal
# ---------------------------------------------------------------------------
@transaction.atomic
def import_dhis2(path_or_buffer, sheet_name=0, save=True,
                 create_missing_org_units=False) -> ImportDHIS2Report:
    """
    Importe un export DHIS2. Retourne un rapport détaillé.

    save=False -> dry-run (parse et compte, n'écrit rien).
    create_missing_org_units=True -> crée les USP absentes (niveau 5) à la volée.
    """
    report = ImportDHIS2Report()

    df = pd.read_excel(path_or_buffer, sheet_name=sheet_name)
    df.columns = [str(c).strip() for c in df.columns]
    report.rows = len(df)

    if PERIOD_COL not in df.columns or ORGUNIT_CODE_COL not in df.columns:
        report.errors.append(
            f"Colonnes obligatoires absentes ({PERIOD_COL}, {ORGUNIT_CODE_COL})."
        )
        return report

    # 1) Analyser les colonnes d'indicateurs
    indicator_cols = [c for c in df.columns if c not in META_COLS]
    col_plan = {}  # nom_colonne -> (DataElement, CategoryOptionCombo)
    for col in indicator_cols:
        p = CP.parse_column(col)
        if p.status == "unknown_indicator":
            report.columns_unknown.append(col)
            continue
        if p.status == "ambiguous":
            report.columns_ambiguous.append(col)
            continue
        if p.status == "missing_dimension":
            report.columns_missing_dim.append(col)
            continue
        coc = _find_coc(p.milieu, p.secteur, p.tranche)
        if coc is None:
            report.columns_missing_dim.append(col)
            continue
        de = _get_or_create_element(p) if save else None
        col_plan[col] = (de, coc, p)
        report.columns_ok += 1

    if not save:
        # Dry-run : estimer le nombre de cellules non vides
        for col in col_plan:
            report.value_cells += int(df[col].notna().sum())
        return report

    # 2) Précharger les org units (par code) + préparer les métadonnées vectorisées
    ou_by_code = {ou.code: ou for ou in OrgUnit.objects.all()}

    # Périodes et codes org unit une fois pour toutes, en tableaux
    periods_arr = df[PERIOD_COL].astype(str).str.strip().to_numpy()
    oucodes_arr = df[ORGUNIT_CODE_COL].astype(str).str.strip().to_numpy()

    # Résoudre les org units ligne par ligne UNE fois (pas par colonne).
    # Les lignes sans org unit connue sont marquées None et sautées partout.
    ou_arr = []
    for code in oucodes_arr:
        ou = ou_by_code.get(code)
        if ou is None:
            if create_missing_org_units:
                ou = OrgUnit.objects.create(name=code, code=code, level=5)
                ou_by_code[code] = ou
            else:
                report.unknown_org_units.add(code)
        ou_arr.append(ou)

    # 3) Dépivoter COLONNE PAR COLONNE (vectorisé), insérer par lots.
    #    Pour chaque colonne : on nettoie toute la série d'un coup, on ne garde
    #    que les cellules non vides, et on crée les DataValue correspondantes.
    buffer = []
    for col, (de, coc, p) in col_plan.items():
        # Nettoyage vectorisé de la colonne entière
        raw = df[col]
        # to_numeric gère la majorité ; on nettoie d'abord les séparateurs FR
        s = (raw.astype(str)
                .str.replace("\xa0", "", regex=False)
                .str.replace(" ", "", regex=False)
                .str.replace(",", ".", regex=False))
        nums = pd.to_numeric(s, errors="coerce")  # NaN si non convertible/vide
        mask = nums.notna().to_numpy()
        if not mask.any():
            continue

        vals = nums.to_numpy()
        idxs = mask.nonzero()[0]  # indices des lignes à valeur non vide

        for i in idxs:
            ou = ou_arr[i]
            if ou is None:
                continue
            report.value_cells += 1
            buffer.append(DataValue(
                data_element=de, org_unit=ou, period=periods_arr[i],
                category_option_combo=coc, value=Decimal(str(vals[i])),
            ))
            if len(buffer) >= BATCH:
                DataValue.objects.bulk_create(buffer, ignore_conflicts=True, batch_size=BATCH)
                report.saved += len(buffer)
                buffer = []

    if buffer:
        DataValue.objects.bulk_create(buffer, ignore_conflicts=True, batch_size=BATCH)
        report.saved += len(buffer)

    return report
