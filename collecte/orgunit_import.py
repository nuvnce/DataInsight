"""
orgunit_import.py — Chargement de la hiérarchie des unités d'organisation
==========================================================================

À placer dans : collecte/orgunit_import.py

RÔLE
----
Lire une extraction DHIS2 et créer/retrouver TOUTE la hiérarchie des unités
d'organisation (pays → région → district → commune → USP), avec parents
correctement chaînés. C'est le prérequis des DEUX imports :
  - import DHIS2 des valeurs (les USP doivent exister)
  - import population (les districts de niveau 3 doivent exister pour le mapping)

CODES
-----
L'extraction ne fournit un code que pour l'USP (organisationunitcode, ex.
TGO0306017). Les niveaux 1 à 4 n'ont pas de code : on en fabrique un STABLE et
DÉTERMINISTE à partir du chemin normalisé des noms. Ainsi un réimport retombe
sur les mêmes codes → aucun doublon (idempotent).

  Niveau 1 (pays)     -> OU_L1_togo
  Niveau 2 (région)   -> OU_L2_togo__plateaux
  Niveau 3 (district) -> OU_L3_togo__plateaux__est_mono
  Niveau 4 (commune)  -> OU_L4_togo__plateaux__est_mono__est_mono_2
  Niveau 5 (USP)      -> le vrai code DHIS2 (TGO0306017)

Le champ `path` et le `level` sont calculés automatiquement par OrgUnit.save().
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd
from django.db import transaction

from metadata.models import OrgUnit


LEVEL_COLS = [
    "orgunitlevel1", "orgunitlevel2", "orgunitlevel3",
    "orgunitlevel4", "orgunitlevel5",
]
CODE_COL = "organisationunitcode"
NAME_COL = "organisationunitname"


def _slug(text) -> str:
    """Nom -> forme stable pour code (minuscules, sans accent, _ à la place des espaces)."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


@dataclass
class OrgUnitReport:
    rows: int = 0
    created: int = 0
    existing: int = 0
    by_level: dict = field(default_factory=dict)   # niveau -> nb total
    errors: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


@transaction.atomic
def import_org_units(path_or_buffer, sheet_name=0, save=True) -> OrgUnitReport:
    """
    Crée/retrouve la hiérarchie complète des org units depuis une extraction DHIS2.

    save=False -> dry-run : compte ce qui serait créé, n'écrit rien.
    """
    report = OrgUnitReport()

    df = pd.read_excel(path_or_buffer, sheet_name=sheet_name,
                       usecols=lambda c: str(c).strip() in set(LEVEL_COLS + [CODE_COL, NAME_COL]))
    df.columns = [str(c).strip() for c in df.columns]
    report.rows = len(df)

    missing = [c for c in LEVEL_COLS if c not in df.columns]
    if missing:
        report.errors.append(f"Colonnes de niveau absentes : {missing}")
        return report

    # Chemins hiérarchiques distincts (une ligne = un chemin racine->USP)
    subset = LEVEL_COLS + ([CODE_COL] if CODE_COL in df.columns else [])
    paths = df[subset].drop_duplicates()

    # Cache : code -> OrgUnit déjà résolu dans CET import (évite les requêtes répétées)
    resolved: dict[str, OrgUnit] = {}

    def get_or_make(code, name, parent, level):
        if code in resolved:
            return resolved[code]
        # 1) Retrouver par code fabriqué (réimport à l'identique)
        ou = OrgUnit.objects.filter(code=code).first()
        # 2) Sinon, retrouver une unité EXISTANTE de même nom/niveau/parent
        #    (cas d'un seed antérieur ou d'autres codes) -> on s'y raccroche
        #    au lieu de dupliquer.
        if ou is None:
            norm = _slug(name)
            for cand in OrgUnit.objects.filter(level=level, parent=parent):
                if _slug(cand.name) == norm:
                    ou = cand
                    break
        if ou is None:
            if save:
                ou = OrgUnit.objects.create(code=code, name=str(name), parent=parent)
                report.created += 1
            else:
                report.created += 1
                resolved[code] = None  # dry-run : on marque comme "à créer"
                return None
        else:
            report.existing += 1
        resolved[code] = ou
        return ou

    for _, row in paths.iterrows():
        names = [row.get(c) for c in LEVEL_COLS]
        # ignorer les niveaux vides en fin de chaîne
        if pd.isna(names[0]):
            continue

        parent = None
        slug_parts = []
        for lvl, name in enumerate(names, start=1):
            if pd.isna(name) or str(name).strip() == "":
                break
            slug_parts.append(_slug(name))
            if lvl == 5:
                # USP : vrai code DHIS2 si présent, sinon code fabriqué
                real = row.get(CODE_COL)
                code = str(real).strip() if (real is not None and not pd.isna(real)) \
                       else "OU_L5_" + "__".join(slug_parts)
            else:
                code = f"OU_L{lvl}_" + "__".join(slug_parts)
            parent = get_or_make(code, name, parent, lvl)
            if parent is None and save:
                break  # sécurité

    # Compte par niveau (état final en base)
    if save:
        for lvl in range(1, 6):
            report.by_level[lvl] = OrgUnit.objects.filter(level=lvl).count()

    return report
