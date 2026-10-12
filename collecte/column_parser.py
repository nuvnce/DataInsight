"""
column_parser.py — Parseur de colonnes DHIS2 pour SIS-Malaria
==============================================================

RÔLE
----
Transformer un nom de colonne DHIS2 (long, avec dimensions encodées dans le
texte) en trois informations structurées :

    "ASC - Nombre de cas suspects de paludisme au niveau communautaire Moins de 5 ans Public"
        -->  indicateur = "cas_suspects"
             tranche    = "moins_5"
             secteur    = "public"

PHILOSOPHIE
-----------
- Détection par mots-clés DISCRIMINANTS : chaque indicateur est défini par des
  mots requis (tous présents) et des mots interdits (aucun présent). Cela évite
  de confondre « cas simple » et « cas grave », ou « suspects » et « suspects testés ».
- TRANSPARENCE : le parseur ne devine jamais en silence. S'il ne trouve pas
  d'indicateur, ou s'il en trouve plusieurs, il le signale dans le résultat.
  C'est toi qui gardes le contrôle des cas douteux.

Ce module est AUTONOME (pas de dépendance Django) pour que tu puisses le tester
seul, en ligne de commande, avec tes propres libellés. On l'intégrera ensuite.

    Test rapide :  python column_parser.py
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
#  1. NORMALISATION DU TEXTE
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """
    Met un libellé sous une forme comparable :
    - minuscules
    - accents retirés (é -> e, ç -> c) : gère « Décédés » vs « Decedes »
    - espaces multiples réduits à un seul (gère les espaces insécables)

    On travaille TOUJOURS sur cette forme normalisée pour comparer, afin que
    les variations d'accents ou d'espaces du fichier DHIS2 ne cassent rien.
    """
    if text is None:
        return ""
    # Décompose les accents puis supprime les marques diacritiques
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Remplace tout groupe d'espaces (y compris insécables \xa0) par un espace simple
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
#  2. DÉFINITION DES DIMENSIONS (âge / statut et secteur)
# ---------------------------------------------------------------------------
# Chaque dimension = une liste de (code_interne, [mots-clés normalisés à chercher]).
# L'ORDRE COMPTE : on teste du plus spécifique au plus général.

TRANCHES = [
    # ⚠️ ORDRE IMPORTANT : "5 ans et plus sans FE" contient "FE" -> tester O5 AVANT FE.
    ("O5",     ["5 ans et plus"]),
    ("FE",     ["femme enceinte", "(fe)"]),   # pas de " fe " seul : évite "sans FE"
    ("U5",     ["moins de 5 ans", "moins de 5"]),
    # si aucune -> "TOT"
]

SECTEURS = [
    ("PUB", ["public"]),
    ("PRV", ["prive"]),   # "privé" -> normalisé "prive"
    # secteur toujours présent dans les données -> pas de valeur par défaut
]

# Milieu : déduit du préfixe (avant le « - ») ou de mots-clés du libellé.
# Codes alignés sur seed_dimensions.py (catégorie MILIEU).
# Simple pour l'instant : communautaire vs formation sanitaire.
MILIEUX = [
    ("COMM", ["asc", "communautaire", "niveau communautaire"]),
    ("FS",   ["c_ext", "consultation externe", "usp"]),
    # Pour affiner plus tard : ajouter HD, HH... ici ET dans seed_dimensions.
]


# ---------------------------------------------------------------------------
#  3. DÉFINITION DES INDICATEURS (le cœur, à enrichir par toi)
# ---------------------------------------------------------------------------
@dataclass
class IndicatorRule:
    """
    Règle de reconnaissance d'un indicateur brut.

    code       : identifiant court et stable (servira de code d'élément de données)
    label      : libellé lisible
    requires   : TOUS ces mots-clés doivent être présents (ET logique)
    excludes   : AUCUN de ces mots-clés ne doit être présent
    priority   : en cas de correspondance multiple, le plus haut gagne
                 (utile pour trancher « suspects testés » > « suspects »)
    """
    code: str
    label: str
    requires: list[str]
    excludes: list[str] = field(default_factory=list)
    priority: int = 0


# ⚠️ LISTE DE DÉPART — à compléter/ajuster avec TON fichier nettoyé.
# Les mots-clés sont en forme normalisée (sans accent, minuscules).
# J'ai mis les indicateurs les plus courants + les distinctions pièges.
INDICATORS = [
    # --- Suspects ---
    IndicatorRule(
        code="SUS_TESTE", label="Cas suspects testés au TDR",
        requires=["suspect", "test"], excludes=[], priority=10,
    ),
    IndicatorRule(
        code="SUS", label="Cas suspects de paludisme",
        requires=["suspect"], excludes=["test"], priority=1,
    ),
    # --- TDR ---
    IndicatorRule(
        code="TDR_POS", label="Cas positifs au TDR",
        requires=["positif", "tdr"], excludes=["effectue"], priority=10,
    ),
    IndicatorRule(
        code="TDR_FAIT", label="TDR effectués",
        requires=["tdr", "effectue"], excludes=[], priority=10,
    ),
    # --- Confirmés (distinction simple / grave essentielle) ---
    IndicatorRule(
        code="CONF_SIMPLE", label="Cas de paludisme simple confirmé",
        requires=["simple", "confirme"], excludes=["grave"], priority=10,
    ),
    IndicatorRule(
        code="CONF_TOT", label="Cas de paludisme confirmé (simple et grave)",
        # Ce libellé contient littéralement « simple et grave » : on le capture
        # explicitement, plutôt que de tenter de l'exclure.
        requires=["confirme", "simple et grave"], excludes=["traite"], priority=8,
    ),
    # --- Graves / référés ---
    IndicatorRule(
        code="GRAVE_REF", label="Cas de paludisme grave référés",
        requires=["grave", "refere"], excludes=[], priority=10,
    ),
    # --- Traités ---
    IndicatorRule(
        code="TRAITE_CTA", label="Cas confirmés traités avec CTA",
        requires=["traite", "cta"], excludes=[], priority=10,
    ),
    # --- Décès ---
    IndicatorRule(
        code="DECES", label="Décès (toutes causes)",
        requires=["decede"], excludes=[], priority=10,
    ),
    # --- Référés (communautaire) ---
    IndicatorRule(
        code="REFERE", label="Malades référés pour paludisme grave",
        requires=["refere", "grave"], excludes=["confirme"], priority=8,
    ),
    # ... À COMPLÉTER : ajoute ici tes autres indicateurs, sur le même modèle.
]


# ---------------------------------------------------------------------------
#  4. RÉSULTAT DU PARSING
# ---------------------------------------------------------------------------
@dataclass
class ParsedColumn:
    """Résultat de l'analyse d'un nom de colonne."""
    raw: str                      # le nom original
    indicator_code: str | None    # code trouvé, ou None si non reconnu
    indicator_label: str | None
    milieu: str                   # "COMM" / "FS" / "" si indéterminé
    tranche: str                  # "U5" / "O5" / "FE" / "TOT"
    secteur: str                  # "PUB" / "PRV" / "" si absent (anomalie)
    status: str                   # "ok" / "unknown_indicator" / "ambiguous" / "missing_dimension"
    candidates: list[str] = field(default_factory=list)  # si ambigu

    @property
    def is_ok(self):
        return self.status == "ok"


# ---------------------------------------------------------------------------
#  5. LE PARSEUR
# ---------------------------------------------------------------------------
def _detect_dimension(norm_text: str, table, default: str) -> str:
    """Cherche la première dimension dont un mot-clé est présent. Sinon `default`."""
    for code, keywords in table:
        for kw in keywords:
            if kw in norm_text:
                return code
    return default


def parse_column(raw_name: str) -> ParsedColumn:
    """
    Analyse un nom de colonne et retourne un ParsedColumn.
    Ne lève jamais d'exception : les problèmes sont dans .status.
    """
    norm = normalize(raw_name)

    # Dimensions (indépendantes de l'indicateur)
    # Âge : absence de mention -> TOT (total tous âges), option explicite (stratégie B).
    # Secteur : toujours présent dans les données -> "" signale une anomalie.
    tranche = _detect_dimension(norm, TRANCHES, default="TOT")
    secteur = _detect_dimension(norm, SECTEURS, default="")
    milieu = _detect_dimension(norm, MILIEUX, default="")

    # Indicateur : on collecte toutes les règles qui matchent
    matches = []
    for rule in INDICATORS:
        if all(kw in norm for kw in rule.requires) and \
           not any(kw in norm for kw in rule.excludes):
            matches.append(rule)

    if not matches:
        return ParsedColumn(raw_name, None, None, milieu, tranche, secteur,
                            status="unknown_indicator")

    # S'il y a plusieurs correspondances, on prend la priorité la plus haute.
    matches.sort(key=lambda r: r.priority, reverse=True)
    best = matches[0]

    # Ambiguïté réelle : deux règles de MÊME priorité maximale
    top = [r for r in matches if r.priority == best.priority]
    if len(top) > 1:
        return ParsedColumn(
            raw_name, best.code, best.label, milieu, tranche, secteur,
            status="ambiguous", candidates=[r.code for r in top],
        )

    # Dimension obligatoire manquante (secteur, ou milieu indéterminé)
    if not secteur or not milieu:
        return ParsedColumn(raw_name, best.code, best.label, milieu, tranche, secteur,
                            status="missing_dimension")

    return ParsedColumn(raw_name, best.code, best.label, milieu, tranche, secteur,
                        status="ok")


# ---------------------------------------------------------------------------
#  6. TEST AUTONOME
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Quelques libellés réels tirés de ton fichier, pour vérifier le comportement.
    exemples = [
        "ASC - Nombre de cas suspects de paludisme au niveau communautaire Moins de 5 ans Public",
        "ASC - Nombre de cas suspects testes avec le test de diagnostic rapide (TDR) au niveau communautaire Femme Enceinte (FE) Public",
        "C_Ext - Nombre de cas de paludisme simple confirme au TDR/GE 5 ans et plus sans FE  Public",
        "C_Ext - Nombre de cas de paludisme confirme au TDR/GE (simple et grave) Prive",
        "C_Ext - Nombre de cas de paludisme grave referes par l USP Moins de 5 ans Prive",
        "C_Ext - Nombre de tests de diagnostic rapide (TDR) positifs Public",
        "C_Ext - Nombre de tests de diagnostic rapide (TDR) effectues FS Public",
        "H_Info1 - Nombre de Décédés (toutes causes) Public",
        "ASC - Nombre de cas de paludisme confirme traites avec CTA recommandes au niveau communautaire Moins de 5 ans Public",
        "PEV - Nombre de doses DTC-HepB-Hib1 administres en fixe Public",  # non prévu -> unknown
    ]

    print(f"{'STATUT':<20} {'INDIC':<12} {'TRANCHE':<15} {'SECTEUR':<8}  COLONNE")
    print("-" * 110)
    for ex in exemples:
        p = parse_column(ex)
        indic = p.indicator_code or "—"
        extra = ""
        if p.status == "ambiguous":
            extra = f"  (candidats: {', '.join(p.candidates)})"
        court = ex if len(ex) < 55 else ex[:52] + "..."
        print(f"{p.status:<20} {indic:<12} {p.tranche:<15} {p.secteur:<8}  {court}{extra}")
