"""
Gestion des périodes par convention de codage (façon DHIS2).

Plutôt qu'une table en base, une période est un code textuel :
  - Mensuel     : "202608"    (août 2026)
  - Trimestriel : "2026Q3"
  - Annuel      : "2026"

Ce module centralise le parsing, la validation et l'affichage de ces codes,
pour éviter d'éparpiller des chaînes magiques dans le reste du projet.
"""
from __future__ import annotations

import datetime as _dt
import re

MONTHLY = "MONTHLY"
QUARTERLY = "QUARTERLY"
YEARLY = "YEARLY"

PERIOD_TYPES = (MONTHLY, QUARTERLY, YEARLY)

_MONTH_NAMES_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_RE_MONTHLY = re.compile(r"^(\d{4})(0[1-9]|1[0-2])$")
_RE_QUARTERLY = re.compile(r"^(\d{4})Q([1-4])$")
_RE_YEARLY = re.compile(r"^(\d{4})$")


class InvalidPeriod(ValueError):
    """Code de période invalide."""


def detect_type(code: str) -> str:
    """Retourne le type de période d'un code, ou lève InvalidPeriod."""
    if _RE_MONTHLY.match(code):
        return MONTHLY
    if _RE_QUARTERLY.match(code):
        return QUARTERLY
    if _RE_YEARLY.match(code):
        return YEARLY
    raise InvalidPeriod(f"Code de période invalide : {code!r}")


def validate(code: str, expected_type: str | None = None) -> str:
    """Valide un code. Si expected_type est donné, vérifie la concordance."""
    ptype = detect_type(code)
    if expected_type and ptype != expected_type:
        raise InvalidPeriod(
            f"La période {code!r} est de type {ptype}, attendu {expected_type}."
        )
    return code


def label(code: str) -> str:
    """Libellé lisible d'un code de période. Ex. '202608' -> 'août 2026'."""
    ptype = detect_type(code)
    if ptype == MONTHLY:
        y, m = int(code[:4]), int(code[4:6])
        return f"{_MONTH_NAMES_FR[m]} {y}"
    if ptype == QUARTERLY:
        y, q = code.split("Q")
        return f"T{q} {y}"
    return code  # YEARLY


def to_code(d: _dt.date, ptype: str = MONTHLY) -> str:
    """Construit le code de période contenant la date d, pour un type donné."""
    if ptype == MONTHLY:
        return f"{d.year}{d.month:02d}"
    if ptype == QUARTERLY:
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    if ptype == YEARLY:
        return f"{d.year}"
    raise InvalidPeriod(f"Type de période inconnu : {ptype!r}")


def recent_codes(ptype: str = MONTHLY, count: int = 12, ref: _dt.date | None = None) -> list[str]:
    """
    Liste des `count` derniers codes de période (du plus récent au plus ancien),
    à partir de la date de référence (aujourd'hui par défaut). Utile pour peupler
    un menu déroulant de sélection de période.
    """
    ref = ref or _dt.date.today()
    codes: list[str] = []
    if ptype == MONTHLY:
        y, m = ref.year, ref.month
        for _ in range(count):
            codes.append(f"{y}{m:02d}")
            m -= 1
            if m == 0:
                m = 12
                y -= 1
    elif ptype == QUARTERLY:
        y, q = ref.year, (ref.month - 1) // 3 + 1
        for _ in range(count):
            codes.append(f"{y}Q{q}")
            q -= 1
            if q == 0:
                q = 4
                y -= 1
    elif ptype == YEARLY:
        y = ref.year
        for _ in range(count):
            codes.append(f"{y}")
            y -= 1
    else:
        raise InvalidPeriod(f"Type de période inconnu : {ptype!r}")
    return codes
