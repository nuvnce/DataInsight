"""
population_import.py — Lecture et agrégation du fichier population RGPH
=======================================================================

À placer dans : collecte/population_import.py

RÔLE
----
Le fichier RGPH est un tableau « pour humains » : en-têtes sur plusieurs lignes,
préfixes « - », un paramètre perdu dans une cellule. Ce module le nettoie et en
extrait une population par DISTRICT × ANNÉE, prête pour la table Population.

STRATÉGIE (voie 1 validée)
--------------------------
On lit la feuille « communes » qui porte les PROJECTIONS ANNUELLES (pop-18 à
pop-27 = 2018 à 2027) et on AGRÈGE les communes de chaque district. Le district
du fichier pop est mappé au district DHIS2 (niveau 3) par NOM NORMALISÉ.

Le mapping réel (nom pop -> OrgUnit) est fait côté Django (voir la fonction
`match_district`). Ici, le parseur reste AUTONOME (pas de dépendance Django)
pour être testable seul : il rend un tableau propre
(region, district, year, value) que la couche Django consommera.

    Test rapide :  python population_import.py chemin/vers/fichier.xlsx
"""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass

import pandas as pd


# ---------------------------------------------------------------------------
#  Normalisation (même logique que column_parser.normalize, dupliquée ici
#  pour garder le module autonome et testable sans Django)
# ---------------------------------------------------------------------------
def normalize(text) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # retire un éventuel préfixe « - » (ex. « - DAGL ») avant traitement
    text = text.strip().lstrip("-").strip()
    # tiret bas/haut assimilé à un espace : « bas-mono » == « bas mono »
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
#  Résultat
# ---------------------------------------------------------------------------
@dataclass
class PopRow:
    region: str        # nom normalisé
    district: str      # nom normalisé
    year: int
    value: int


@dataclass
class PopReport:
    rows: list          # list[PopRow]
    districts: set      # districts distincts trouvés
    years: list         # années trouvées
    warnings: list      # anomalies non bloquantes

    def summary(self):
        return (f"{len(self.rows)} lignes (district×année) | "
                f"{len(self.districts)} districts | "
                f"années {min(self.years)}–{max(self.years)}")


# ---------------------------------------------------------------------------
#  Détection des colonnes annuelles « pop-18 » -> 2018
# ---------------------------------------------------------------------------
_YEAR_COL = re.compile(r"pop[-_ ]?(\d{2})$", re.IGNORECASE)

def _year_from_col(col) -> int | None:
    m = _YEAR_COL.search(str(col).strip())
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy  # 18 -> 2018


# ---------------------------------------------------------------------------
#  Parsing de la feuille « communes » (projections annuelles)
# ---------------------------------------------------------------------------
def parse_communes_sheet(path_or_buffer, sheet_name="Donnee_aggregée communes") -> PopReport:
    """
    Lit la feuille communes, repère la ligne d'en-tête réelle
    (celle contenant Region/District/Commune + pop-XX), agrège au district.
    """
    warnings = []

    # On lit SANS en-tête pour retrouver nous-mêmes la vraie ligne d'en-tête.
    raw = pd.read_excel(path_or_buffer, sheet_name=sheet_name, header=None)

    # Trouver la ligne d'en-tête : celle qui contient 'district' ET une col pop-XX
    header_row = None
    for i in range(min(15, len(raw))):
        cells = [normalize(c) for c in raw.iloc[i].tolist()]
        joined = " ".join(cells)
        if "district" in joined and any(_YEAR_COL.search(str(c)) for c in raw.iloc[i].tolist()):
            header_row = i
            break
    if header_row is None:
        raise ValueError(
            "Ligne d'en-tête introuvable dans la feuille communes "
            "(attendu : Region/District/Commune + colonnes pop-XX)."
        )

    df = pd.read_excel(path_or_buffer, sheet_name=sheet_name, header=header_row)
    df.columns = [str(c).strip() for c in df.columns]

    # Repérer les colonnes clés par nom normalisé
    col_region = col_district = col_commune = None
    year_cols = {}  # nom_colonne -> année
    for c in df.columns:
        n = normalize(c)
        if n == "region":
            col_region = c
        elif n == "district":
            col_district = c
        elif n == "commune":
            col_commune = c
        else:
            y = _year_from_col(c)
            if y:
                year_cols[c] = y

    if col_district is None:
        raise ValueError("Colonne « District » introuvable.")
    if not year_cols:
        raise ValueError("Aucune colonne annuelle « pop-XX » trouvée.")

    # Nettoyer : garder les lignes ayant un district non vide
    df = df[df[col_district].notna()].copy()

    # Agrégation par (region, district) -> somme des communes, pour chaque année
    def clean_int(v):
        if pd.isna(v):
            return 0
        s = str(v).replace("\xa0", "").replace(" ", "").replace(",", "")
        try:
            return int(float(s))
        except ValueError:
            return 0

    rows = []
    districts = set()
    grp_cols = [c for c in (col_region, col_district) if c]
    for keys, sub in df.groupby([df[c] for c in grp_cols]):
        if len(grp_cols) == 2:
            region_raw, district_raw = keys
        else:
            region_raw, district_raw = "", keys
        region = normalize(region_raw)
        district = normalize(district_raw)
        districts.add(district)
        for col, year in year_cols.items():
            total = int(sub[col].map(clean_int).sum())
            if total > 0:
                rows.append(PopRow(region, district, year, total))

    years = sorted({r.year for r in rows})
    return PopReport(rows=rows, districts=districts, years=years, warnings=warnings)


# ---------------------------------------------------------------------------
#  Test autonome en ligne de commande
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage : python population_import.py <fichier.xlsx>")
        sys.exit(1)
    rep = parse_communes_sheet(path)
    print(rep.summary())
    print("\nDistricts trouvés :")
    for d in sorted(rep.districts):
        print("  -", d)
    print("\nAperçu (10 premières lignes) :")
    for r in rep.rows[:10]:
        print(f"  {r.region:<12} {r.district:<15} {r.year}  {r.value:>10,}")
