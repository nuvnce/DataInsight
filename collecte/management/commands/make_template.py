"""
make_template — génère le modèle Excel de collecte (couche 1 amont)
====================================================================

Produit un classeur vide à remplir, structuré pour la fiabilisation :

  - un onglet par secteur : « Public » et « Privé » ;
  - une ligne par (district × période) ;
  - une colonne par (élément × tranche d'âge), codes alignés sur le parseur
    DHIS2 (column_parser.py) — même vocabulaire que le reste du système ;
  - un onglet « Légende » qui explique les codes et montre une ligne d'exemple.

Le convertisseur DHIS2 → template (brique suivante) produira exactement cette
structure : le modèle est la cible, la moulinette la remplit.

Usage :
    python manage.py make_template                      # -> modele_collecte.xlsx
    python manage.py make_template --out chemin.xlsx
"""
from pathlib import Path

from django.core.management.base import BaseCommand

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


# Éléments retenus pour les quatre familles du projet (confirmés, testés,
# décès, hospitalisés) + traités. Codes = ceux émis par le parseur DHIS2.
# (code, libellé). HOSP est prévu dans la structure même si le parseur ne le
# reconnaît pas encore — l'extension viendra avec le convertisseur.
ELEMENTS = [
    ("SUS",         "Cas suspects de paludisme"),
    ("SUS_TESTE",   "Cas suspects testés au TDR"),
    ("TDR_FAIT",    "TDR effectués"),
    ("TDR_POS",     "Cas positifs au TDR"),
    ("CONF_TOT",    "Cas confirmés (simple et grave)"),
    ("CONF_SIMPLE", "Cas simples confirmés"),
    ("CONF_GRAVE",  "Cas graves confirmés"),
    ("GRAVE_REF",   "Cas graves référés"),
    ("TRAITE_CTA",  "Cas confirmés traités au CTA"),
    ("HOSP",        "Cas hospitalisés pour paludisme"),
    ("DECES",       "Décès dus au paludisme"),
]

# Tranches d'âge (désagrégation). TOT = tous âges confondus.
AGES = [
    ("TOT", "Tous âges"),
    ("U5",  "Moins de 5 ans"),
    ("O5",  "5 ans et plus (hors FE)"),
    ("FE",  "Femme enceinte"),
]

SECTORS = [("Public", "PUB"), ("Privé", "PRV")]

NAVY = "1F3864"
LIGHT = "D9E2F3"
ACCENT = "C55A11"


def _style_header(cell, bg=NAVY, fg="FFFFFF", bold=True):
    cell.font = Font(name="Arial", size=10, bold=bold, color=fg)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _build_sheet(ws, sector_label):
    """Construit un onglet secteur : 2 lignes d'en-tête + colonnes fixes."""
    thin = Side(style="thin", color="BFC9DF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Colonnes fixes (identité de la ligne)
    fixed = ["District", "Code district", "Période"]
    # Colonnes de données : (élément × âge)
    # Ligne 1 = famille/élément (fusionnée sur ses 4 âges) ; ligne 2 = âge.
    col = 1
    for name in fixed:
        c = ws.cell(row=1, column=col, value=name)
        _style_header(c)
        ws.cell(row=2, column=col, value="").fill = PatternFill("solid", fgColor=LIGHT)
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
        col += 1

    data_start = col
    for code, label in ELEMENTS:
        start = col
        for age_code, age_label in AGES:
            top = ws.cell(row=1, column=col, value=f"{code}")
            _style_header(top, bg=NAVY)
            sub = ws.cell(row=2, column=col, value=f"{age_code}")
            _style_header(sub, bg="2E5496", bold=False)
            col += 1
        # fusionne la 1re ligne sur les 4 âges de l'élément
        ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=col - 1)

    # Largeurs
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 12
    # période toujours en texte (code AAAAMM), sur toute la colonne utile
    for rr in range(3, 200):
        ws.cell(row=rr, column=3).number_format = "@"
    for i in range(data_start, col):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 9

    # Ligne d'exemple (réaliste, pour montrer le format — à effacer avant usage)
    ex = ["Golfe", "GOLFE", "202401"]
    # quelques valeurs cohérentes pour l'exemple sur TOT uniquement
    ws.cell(row=3, column=1, value=ex[0])
    ws.cell(row=3, column=2, value=ex[1])
    pcell = ws.cell(row=3, column=3, value=ex[2])
    pcell.number_format = "@"   # période en texte, pas en nombre (202401, pas 202401.0)
    example_tot = {"SUS": 1200, "SUS_TESTE": 1150, "TDR_FAIT": 1150, "TDR_POS": 800,
                   "CONF_TOT": 820, "CONF_SIMPLE": 760, "CONF_GRAVE": 60,
                   "GRAVE_REF": 55, "TRAITE_CTA": 790, "HOSP": 45, "DECES": 3}
    col = data_start
    for code, _ in ELEMENTS:
        for age_code, _ in AGES:
            if age_code == "TOT":
                ws.cell(row=3, column=col, value=example_tot.get(code, ""))
            col += 1
    for c in range(1, col):
        cell = ws.cell(row=3, column=c)
        cell.font = Font(name="Arial", size=9, italic=True, color="888888")
        cell.border = border

    ws.freeze_panes = "D3"
    ws.cell(row=4, column=1, value="(ligne 3 = exemple à effacer avant de remplir)").font = \
        Font(name="Arial", size=8, italic=True, color=ACCENT)


def _build_legend(wb):
    ws = wb.create_sheet("Légende", 0)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 40

    title = ws.cell(row=1, column=1, value="DataInsight — modèle de collecte paludisme")
    title.font = Font(name="Arial", size=14, bold=True, color=NAVY)
    ws.merge_cells("A1:C1")

    intro = [
        "Un onglet par secteur : « Public » et « Privé ».",
        "Une ligne par district et par période (code période : AAAAMM, ex. 202401).",
        "Une colonne par élément × tranche d'âge. Remplir les cases numériques.",
        "TOT = tous âges ; U5 = moins de 5 ans ; O5 = 5 ans et plus hors FE ; FE = femme enceinte.",
        "La ligne d'exemple (en italique) est à effacer avant de remplir.",
    ]
    r = 3
    for line in intro:
        c = ws.cell(row=r, column=1, value="•")
        c.font = Font(name="Arial", size=10, color=ACCENT)
        d = ws.cell(row=r, column=2, value=line)
        d.font = Font(name="Arial", size=10)
        d.alignment = Alignment(wrap_text=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        r += 1

    r += 1
    h1 = ws.cell(row=r, column=1, value="Code")
    h2 = ws.cell(row=r, column=2, value="Élément de données")
    h3 = ws.cell(row=r, column=3, value="Famille")
    for c in (h1, h2, h3):
        _style_header(c)
    r += 1

    family = {
        "SUS": "Testés", "SUS_TESTE": "Testés", "TDR_FAIT": "Testés", "TDR_POS": "Testés",
        "CONF_TOT": "Confirmés", "CONF_SIMPLE": "Confirmés", "CONF_GRAVE": "Confirmés",
        "GRAVE_REF": "Confirmés", "TRAITE_CTA": "Confirmés",
        "HOSP": "Hospitalisés", "DECES": "Décès",
    }
    for code, label in ELEMENTS:
        ws.cell(row=r, column=1, value=code).font = Font(name="Arial", size=10, bold=True)
        ws.cell(row=r, column=2, value=label).font = Font(name="Arial", size=10)
        ws.cell(row=r, column=3, value=family.get(code, "")).font = Font(name="Arial", size=10)
        r += 1


class Command(BaseCommand):
    help = "Génère le modèle Excel de collecte (onglets Public/Privé + Légende)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="modele_collecte.xlsx",
                            help="Chemin du fichier .xlsx à produire.")

    def handle(self, *args, **opts):
        wb = openpyxl.Workbook()
        # supprime la feuille par défaut, on crée les nôtres
        default = wb.active
        wb.remove(default)

        for label, code in SECTORS:
            ws = wb.create_sheet(label)
            _build_sheet(ws, label)

        _build_legend(wb)

        out = Path(opts["out"])
        wb.save(out)
        n_cols = len(ELEMENTS) * len(AGES)
        self.stdout.write(self.style.SUCCESS(
            f"✓ Modèle écrit : {out}  "
            f"({len(SECTORS)} onglets secteur, {len(ELEMENTS)} éléments × "
            f"{len(AGES)} âges = {n_cols} colonnes de données)."
        ))
