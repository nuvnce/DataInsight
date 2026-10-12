"""
Génération de templates Excel et import de fichiers (Tranche 3b).

Structure du template (.xlsx)
-----------------------------
Feuille « Données » :
  - Ligne 1 : libellés lisibles (élément — désagrégation)   [pour l'humain]
  - Ligne 2 : clés techniques  "CODE_ELEMENT::CODE_COC"      [masquée, appariement]
  - Colonnes A/B : code et nom de l'unité d'organisation
  - Lignes suivantes : une par unité d'organisation du formulaire

L'appariement à l'import se fait par la clé technique (ligne 2), jamais par
le libellé : le nom peut changer, le code est stable.

Feuille « Informations » : métadonnées du template (formulaire, période, date).
"""
from __future__ import annotations

import datetime as _dt
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from metadata.models import OrgUnit, DataElement, CategoryOptionCombo
from .models import DataSet


KEY_SEP = "::"
HEADER_ROW = 1
KEY_ROW = 2
FIRST_DATA_ROW = 3
OU_CODE_COL = 1
OU_NAME_COL = 2
FIRST_VALUE_COL = 3

_NAVY = "1F3864"
_LIGHT = "D9E2F3"


def _columns_for(dataset: DataSet | None):
    """
    Retourne la liste ordonnée des colonnes de valeurs :
    tuples (data_element, category_option_combo, header_label, key).
    """
    if dataset:
        elements = dataset.data_elements.select_related("category_combo").order_by("name")
    else:
        elements = DataElement.objects.select_related("category_combo").order_by("name")

    cols = []
    for de in elements:
        cocs = CategoryOptionCombo.objects.filter(combo=de.category_combo).order_by("name")
        for coc in cocs:
            # Libellé lisible : « Cas confirmés » ou « Cas confirmés — Masculin, <5 »
            is_default = coc.combo.is_default or coc.options.count() == 0
            label = de.name if is_default else f"{de.name} — {coc.name}"
            key = f"{de.code}{KEY_SEP}{coc.id}"
            cols.append((de, coc, label, key))
    return cols


def _org_units_for(dataset: DataSet | None):
    if dataset and dataset.org_units.exists():
        return dataset.org_units.order_by("name")
    return OrgUnit.objects.filter(children__isnull=True).order_by("name")


def build_template(dataset: DataSet | None, period: str | None = None) -> bytes:
    """Construit le template .xlsx et retourne son contenu binaire."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Données"

    cols = _columns_for(dataset)
    org_units = _org_units_for(dataset)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=_NAVY)
    key_font = Font(size=8, italic=True, color="888888")
    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # En-têtes fixes
    ws.cell(HEADER_ROW, OU_CODE_COL, "Code unité")
    ws.cell(HEADER_ROW, OU_NAME_COL, "Unité d'organisation")
    ws.cell(KEY_ROW, OU_CODE_COL, "__ou_code__")
    ws.cell(KEY_ROW, OU_NAME_COL, "__ou_name__")

    # En-têtes de valeurs
    for i, (de, coc, label, key) in enumerate(cols):
        c = FIRST_VALUE_COL + i
        hc = ws.cell(HEADER_ROW, c, label)
        hc.font = header_font
        hc.fill = header_fill
        hc.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        hc.border = border
        kc = ws.cell(KEY_ROW, c, key)
        kc.font = key_font

    for c in (OU_CODE_COL, OU_NAME_COL):
        ws.cell(HEADER_ROW, c).font = header_font
        ws.cell(HEADER_ROW, c).fill = header_fill
        ws.cell(HEADER_ROW, c).border = border

    # Lignes : une par unité d'organisation, colonnes code/nom pré-remplies
    for r, ou in enumerate(org_units, start=FIRST_DATA_ROW):
        ws.cell(r, OU_CODE_COL, ou.code)
        ws.cell(r, OU_NAME_COL, ou.name)

    # Mise en forme : masquer la ligne des clés, figer les volets, largeurs
    ws.row_dimensions[KEY_ROW].hidden = True
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, FIRST_VALUE_COL)
    ws.column_dimensions[get_column_letter(OU_CODE_COL)].width = 16
    ws.column_dimensions[get_column_letter(OU_NAME_COL)].width = 28
    for i in range(len(cols)):
        ws.column_dimensions[get_column_letter(FIRST_VALUE_COL + i)].width = 16

    # Feuille d'informations
    info = wb.create_sheet("Informations")
    info["A1"] = "Formulaire"
    info["B1"] = dataset.name if dataset else "(saisie libre — tous les éléments)"
    info["A2"] = "Type de période"
    info["B2"] = dataset.get_period_type_display() if dataset else "Mensuel"
    info["A3"] = "Période"
    info["B3"] = period or "(à renseigner)"
    info["A4"] = "Généré le"
    info["B4"] = _dt.date.today().isoformat()
    info["A5"] = "Consigne"
    info["B5"] = ("Renseignez les valeurs numériques. Ne modifiez pas la ligne masquée "
                  "(clés techniques) ni les colonnes Code unité.")
    for r in range(1, 6):
        info.cell(r, 1).font = Font(bold=True, color=_NAVY)
    info.column_dimensions["A"].width = 18
    info.column_dimensions["B"].width = 60

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ImportReport:
    """Compte-rendu d'un import : compteurs et anomalies détaillées."""
    def __init__(self):
        self.saved = 0
        self.cleared = 0
        self.skipped = 0
        self.errors: list[str] = []
        self.unknown_columns: list[str] = []
        self.unknown_org_units: list[str] = []

    @property
    def ok(self):
        return not self.errors and not self.unknown_columns and not self.unknown_org_units

    def as_dict(self):
        return {
            "saved": self.saved, "cleared": self.cleared, "skipped": self.skipped,
            "errors": self.errors, "unknown_columns": self.unknown_columns,
            "unknown_org_units": self.unknown_org_units, "ok": self.ok,
        }


def parse_and_import(file_obj, dataset: DataSet | None, period: str,
                     save: bool = True) -> ImportReport:
    """
    Lit un fichier .xlsx conforme au template et importe les valeurs pour la
    période donnée. Mode STRICT : appariement par clé technique (ligne 2),
    colonnes inconnues signalées mais non créées.

    Si save=False : simulation (dry-run) — valide et compte sans écrire.
    """
    from decimal import Decimal, InvalidOperation
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from . import periods as P
    from .models import DataValue

    report = ImportReport()

    # Validation de la période
    try:
        expected = dataset.period_type if dataset else None
        P.validate(period, expected_type=expected)
    except P.InvalidPeriod as e:
        report.errors.append(f"Période : {e}")
        return report

    try:
        wb = load_workbook(file_obj, data_only=True, read_only=True)
    except Exception as e:
        report.errors.append(f"Fichier illisible : {e}")
        return report

    if "Données" not in wb.sheetnames:
        report.errors.append("Feuille « Données » introuvable dans le fichier.")
        return report
    ws = wb["Données"]

    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < FIRST_DATA_ROW:
        report.errors.append("Le fichier ne contient aucune ligne de données.")
        return report

    key_row = rows[KEY_ROW - 1]  # ligne des clés techniques (index 0-based)

    # Cartographie colonne -> (data_element, category_option_combo)
    col_map = {}
    valid_elements = None
    if dataset:
        valid_elements = set(dataset.data_elements.values_list("code", flat=True))

    for col_idx, key in enumerate(key_row):
        if col_idx in (OU_CODE_COL - 1, OU_NAME_COL - 1):
            continue
        if not key or KEY_SEP not in str(key):
            if key and str(key).strip():
                report.unknown_columns.append(str(key))
            continue
        de_code, coc_id = str(key).split(KEY_SEP, 1)
        # Mode strict : si un formulaire est donné, la colonne doit lui appartenir
        if valid_elements is not None and de_code not in valid_elements:
            report.unknown_columns.append(de_code)
            continue
        de = DataElement.objects.filter(code=de_code).first()
        coc = CategoryOptionCombo.objects.filter(pk=coc_id).first()
        if not de or not coc:
            report.unknown_columns.append(str(key))
            continue
        col_map[col_idx] = (de, coc)

    # Précharge des unités d'organisation par code
    ou_by_code = {ou.code: ou for ou in OrgUnit.objects.all()}

    # Traitement des lignes de données
    to_upsert = []   # (de, ou, coc, value)
    to_clear = []    # (de, ou, coc)

    for r in rows[FIRST_DATA_ROW - 1:]:
        if r is None or all(c is None for c in r):
            continue
        ou_code = r[OU_CODE_COL - 1]
        if not ou_code:
            continue
        ou = ou_by_code.get(str(ou_code).strip())
        if not ou:
            report.unknown_org_units.append(str(ou_code))
            continue

        for col_idx, (de, coc) in col_map.items():
            raw = r[col_idx] if col_idx < len(r) else None
            if raw is None or str(raw).strip() == "":
                to_clear.append((de, ou, coc))
                continue
            try:
                value = Decimal(str(raw).replace(",", "."))
            except (InvalidOperation, ValueError):
                report.errors.append(
                    f"{ou.code} / {de.code} : valeur non numérique ({raw!r})."
                )
                report.skipped += 1
                continue

            dv = DataValue(data_element=de, org_unit=ou, period=period,
                           category_option_combo=coc, value=value)
            try:
                dv.clean()  # validation métier sans contrainte d'unicité
            except ValidationError as e:
                msg = "; ".join(
                    m for msgs in getattr(e, "message_dict", {}).values() for m in msgs
                ) or str(e)
                report.errors.append(f"{ou.code} / {de.code} : {msg}")
                report.skipped += 1
                continue
            to_upsert.append((de, ou, coc, value))

    if not save:
        # Dry-run : on rapporte ce qui serait fait sans écrire
        report.saved = len(to_upsert)
        report.cleared = 0  # on ne compte pas les suppressions en simulation
        return report

    with transaction.atomic():
        for de, ou, coc, value in to_upsert:
            DataValue.objects.update_or_create(
                data_element=de, org_unit=ou, period=period,
                category_option_combo=coc, defaults={"value": value},
            )
            report.saved += 1
        for de, ou, coc in to_clear:
            deleted, _ = DataValue.objects.filter(
                data_element=de, org_unit=ou, period=period,
                category_option_combo=coc,
            ).delete()
            report.cleared += deleted

    return report


def analyze_columns(file_obj, dataset: DataSet | None):
    """
    Analyse les colonnes d'un fichier (mode souple) SANS importer.
    Retourne un dict :
      - known   : liste de (key, de_code, coc_id, label) reconnues
      - unknown : liste de {index, raw_key, header, de_code, coc_id}
                  pour les colonnes non reconnues (à arbitrer par l'utilisateur)
      - error   : message si le fichier est illisible
    Une colonne est « inconnue » si sa clé ne correspond à aucun élément existant
    (code absent du référentiel), ou si, avec un formulaire, l'élément n'appartient
    pas à ce formulaire.
    """
    result = {"known": [], "unknown": [], "error": None}
    try:
        wb = load_workbook(file_obj, data_only=True, read_only=True)
    except Exception as e:
        result["error"] = f"Fichier illisible : {e}"
        return result
    if "Données" not in wb.sheetnames:
        result["error"] = "Feuille « Données » introuvable."
        return result
    ws = wb["Données"]
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < KEY_ROW:
        result["error"] = "Fichier incomplet (pas de ligne de clés)."
        return result

    header_row = rows[HEADER_ROW - 1]
    key_row = rows[KEY_ROW - 1]
    valid_elements = None
    if dataset:
        valid_elements = set(dataset.data_elements.values_list("code", flat=True))

    for idx, key in enumerate(key_row):
        if idx in (OU_CODE_COL - 1, OU_NAME_COL - 1):
            continue
        header = header_row[idx] if idx < len(header_row) else None
        if not key or KEY_SEP not in str(key):
            if key and str(key).strip():
                result["unknown"].append({
                    "index": idx, "raw_key": str(key),
                    "header": str(header) if header else str(key),
                    "de_code": "", "coc_id": "",
                })
            continue
        de_code, coc_id = str(key).split(KEY_SEP, 1)
        de = DataElement.objects.filter(code=de_code).first()
        coc = CategoryOptionCombo.objects.filter(pk=coc_id).first()
        is_known = bool(de and coc)
        if valid_elements is not None and de_code not in valid_elements:
            is_known = False
        if is_known:
            result["known"].append((str(key), de_code, coc_id, str(header)))
        else:
            result["unknown"].append({
                "index": idx, "raw_key": str(key),
                "header": str(header) if header else str(key),
                "de_code": de_code, "coc_id": coc_id,
            })
    return result


def create_element_from_column(name: str, code: str) -> DataElement:
    """
    Crée un nouvel élément de données rattaché au combo par défaut, à partir
    d'une colonne inconnue validée par l'utilisateur (mode souple).
    """
    from metadata.models import CategoryCombo, CategoryOptionCombo
    default_combo = CategoryCombo.objects.filter(is_default=True).first()
    if default_combo is None:
        default_combo = CategoryCombo.objects.create(
            name="(par défaut)", code="default", is_default=True
        )
        CategoryOptionCombo.objects.create(combo=default_combo, name="(défaut)")
    de, created = DataElement.objects.get_or_create(
        code=code,
        defaults={"name": name, "category_combo": default_combo},
    )
    return de
