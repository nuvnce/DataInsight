"""
Moteur de complétude — couche 1, contrôle 1/3
==============================================

Première des trois portes de fiabilisation. Elle répond à une seule question,
cellule par cellule : **la valeur est-elle présente ?**

Principe directeur (couche 1) : le moteur *détecte et propose*, il ne corrige
jamais. Il écrit des anomalies (`Anomaly`) avec une valeur suggérée ; l'humain
décide. Aucune ligne `DataValue` n'est modifiée ici.

Ce que fait le moteur
----------------------
1. Il établit la **grille attendue** : pour un lot, le produit
   (unités d'organisation × périodes × éléments × désagrégations) qui *devrait*
   être renseigné.
2. Il regarde ce qui est **réellement présent** dans `DataValue`.
3. Chaque case attendue mais vide est une **valeur manquante**.
4. Le **score de complétude** = % de cases remplies. Sous le seuil (85 % par
   défaut, soit > 15 % de trous), le contrôle est en échec.
5. Pour chaque trou, il **propose une valeur** par l'une des quatre méthodes :
   moyenne mobile, médiane, interpolation linéaire, comparaison saisonnière.

Les séries temporelles utilisées pour l'imputation sont construites par
(unité × élément × désagrégation), ordonnées par période.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from collecte import periods
from collecte.models import DataValue


# --- Méthodes d'imputation proposables (valeurs stockées dans Anomaly) --------
METHOD_MOVING_AVG = "moyenne mobile"
METHOD_MEDIAN = "médiane"
METHOD_INTERPOLATION = "interpolation linéaire"
METHOD_SEASONAL = "comparaison saisonnière"

DEFAULT_THRESHOLD = Decimal("85")   # % de remplissage minimal (> 15 % de trous = échec)
DEFAULT_WINDOW = 3                  # fenêtre de la moyenne mobile


# ---------------------------------------------------------------------------
#  Résultats (objets simples, découplés de la base — faciles à tester)
# ---------------------------------------------------------------------------
@dataclass
class MissingCell:
    """Une case attendue mais vide, avec la valeur proposée pour la combler."""
    org_unit_id: int
    period: str
    data_element_id: int
    coc_id: int
    suggested_value: Decimal | None = None
    method: str = ""
    note: str = ""


@dataclass
class CompletenessReport:
    """Bilan d'un passage de complétude sur un périmètre."""
    expected: int = 0
    present: int = 0
    missing_cells: list[MissingCell] = field(default_factory=list)

    @property
    def missing(self) -> int:
        return len(self.missing_cells)

    @property
    def score(self) -> Decimal:
        """% de cases remplies. 100 si rien n'est attendu."""
        if self.expected == 0:
            return Decimal("100")
        return (Decimal(self.present) / Decimal(self.expected) * 100).quantize(Decimal("0.01"))

    def passes(self, threshold: Decimal = DEFAULT_THRESHOLD) -> bool:
        return self.score >= threshold


# ---------------------------------------------------------------------------
#  Imputation : les quatre méthodes, sur une série (période -> valeur)
# ---------------------------------------------------------------------------
def _sorted_known(series: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
    """Points connus de la série, triés par période croissante."""
    return sorted(series.items(), key=lambda kv: kv[0])


def impute_moving_average(series: dict[str, Decimal], target: str,
                          window: int = DEFAULT_WINDOW) -> Decimal | None:
    """Moyenne des `window` dernières valeurs connues *avant* la période cible."""
    before = [v for p, v in _sorted_known(series) if p < target]
    if not before:
        return None
    sample = before[-window:]
    return (sum(sample) / len(sample)).quantize(Decimal("0.01"))


def impute_median(series: dict[str, Decimal], target: str) -> Decimal | None:
    """Médiane de toutes les valeurs connues de la série (robuste aux extrêmes)."""
    vals = [v for _, v in series.items()]
    if not vals:
        return None
    return Decimal(str(statistics.median(vals))).quantize(Decimal("0.01"))


def impute_interpolation(series: dict[str, Decimal], target: str) -> Decimal | None:
    """
    Interpolation linéaire entre le point connu juste avant et juste après la
    cible, pondérée par la distance en nombre de périodes. Sans encadrement des
    deux côtés, on ne propose rien (on ne veut pas extrapoler ici).
    """
    known = _sorted_known(series)
    ordered_periods = [p for p, _ in known]
    before = [(p, v) for p, v in known if p < target]
    after = [(p, v) for p, v in known if p > target]
    if not before or not after:
        return None

    p0, v0 = before[-1]
    p1, v1 = after[0]

    # Distance en périodes : on a besoin de l'axe complet (connu + cible) pour
    # compter les pas. On reconstruit l'ordre en insérant la cible.
    axis = sorted(set(ordered_periods) | {target})
    i0, it, i1 = axis.index(p0), axis.index(target), axis.index(p1)
    span = i1 - i0
    if span == 0:
        return v0
    ratio = Decimal(it - i0) / Decimal(span)
    return (v0 + (v1 - v0) * ratio).quantize(Decimal("0.01"))


def impute_seasonal(series: dict[str, Decimal], target: str) -> Decimal | None:
    """
    Comparaison saisonnière : valeur de la même période l'an passé (et, à défaut,
    l'année suivante). Ne vaut que pour les périodes mensuelles/trimestrielles.
    """
    try:
        ptype = periods.detect_type(target)
    except periods.InvalidPeriod:
        return None
    if ptype == periods.YEARLY:
        return None

    def shift_year(code: str, delta: int) -> str:
        if ptype == periods.MONTHLY:
            return f"{int(code[:4]) + delta}{code[4:6]}"
        # QUARTERLY : "2026Q3"
        y, q = code.split("Q")
        return f"{int(y) + delta}Q{q}"

    for delta in (-1, 1):
        cand = series.get(shift_year(target, delta))
        if cand is not None:
            return cand.quantize(Decimal("0.01"))
    return None


# Ordre de préférence : on prend la première méthode qui donne un résultat.
# Saisonnier d'abord (le plus parlant en épidémiologie du paludisme, forte
# saisonnalité), puis moyenne mobile, interpolation, médiane en dernier recours.
_METHOD_CHAIN = [
    (METHOD_SEASONAL, impute_seasonal),
    (METHOD_MOVING_AVG, impute_moving_average),
    (METHOD_INTERPOLATION, impute_interpolation),
    (METHOD_MEDIAN, impute_median),
]


def suggest(series: dict[str, Decimal], target: str,
            window: int = DEFAULT_WINDOW) -> tuple[Decimal | None, str]:
    """
    Essaie les méthodes dans l'ordre de préférence et renvoie
    (valeur_suggérée, méthode) pour la première qui aboutit, sinon (None, "").
    """
    for name, fn in _METHOD_CHAIN:
        if fn is impute_moving_average:
            val = fn(series, target, window)
        else:
            val = fn(series, target)
        if val is not None:
            return val, name
    return None, ""


# ---------------------------------------------------------------------------
#  Analyse d'un périmètre
# ---------------------------------------------------------------------------
def analyze(org_unit_ids, periods_list, data_element_ids, coc_ids,
            window: int = DEFAULT_WINDOW) -> CompletenessReport:
    """
    Compare la grille attendue (produit des quatre dimensions) aux valeurs
    réellement présentes, et propose une valeur pour chaque trou.

    Les dimensions sont fournies explicitement : c'est l'appelant (la vue, la
    commande) qui décide du périmètre d'un lot — on ne devine pas ici ce qui
    « devrait » exister.
    """
    org_unit_ids = list(org_unit_ids)
    periods_list = sorted(set(periods_list))
    data_element_ids = list(data_element_ids)
    coc_ids = list(coc_ids)

    report = CompletenessReport()
    report.expected = (len(org_unit_ids) * len(periods_list)
                       * len(data_element_ids) * len(coc_ids))
    if report.expected == 0:
        return report

    # Présence réelle : un seul passage en base sur le périmètre.
    present_rows = DataValue.objects.filter(
        org_unit_id__in=org_unit_ids,
        period__in=periods_list,
        data_element_id__in=data_element_ids,
        category_option_combo_id__in=coc_ids,
    ).values_list("org_unit_id", "period", "data_element_id",
                  "category_option_combo_id", "value")

    present_keys: set[tuple] = set()
    # Séries pour l'imputation : clé (ou, de, coc) -> { période: valeur }
    series_map: dict[tuple, dict[str, Decimal]] = {}
    for ou, per, de, coc, val in present_rows:
        present_keys.add((ou, per, de, coc))
        series_map.setdefault((ou, de, coc), {})[per] = val

    report.present = len(present_keys)

    # Les trous : chaque case attendue absente des présences.
    for ou in org_unit_ids:
        for de in data_element_ids:
            for coc in coc_ids:
                serie = series_map.get((ou, de, coc), {})
                for per in periods_list:
                    if (ou, per, de, coc) in present_keys:
                        continue
                    val, method = suggest(serie, per, window)
                    note = "" if val is not None else "aucune valeur voisine pour proposer"
                    report.missing_cells.append(MissingCell(
                        org_unit_id=ou, period=per, data_element_id=de, coc_id=coc,
                        suggested_value=val, method=method, note=note,
                    ))
    return report


# ---------------------------------------------------------------------------
#  Persistance : écrit le QualityCheck et les Anomaly (sans toucher DataValue)
# ---------------------------------------------------------------------------
def run_and_record(version, org_unit_ids, periods_list, data_element_ids, coc_ids,
                   threshold: Decimal = DEFAULT_THRESHOLD,
                   window: int = DEFAULT_WINDOW):
    """
    Lance l'analyse sur une `BatchVersion` et enregistre le résultat :
    un `QualityCheck` (étape complétude) et une `Anomaly` par trou.

    Renvoie le `QualityCheck` créé. Ne modifie aucune `DataValue` : le moteur
    propose, l'humain décidera dans l'écran de traitement (brique suivante).
    """
    from .models import QualityCheck, Anomaly

    report = analyze(org_unit_ids, periods_list, data_element_ids, coc_ids, window)
    passed = report.passes(threshold)

    check = QualityCheck.objects.create(
        version=version,
        step=QualityCheck.Step.COMPLETENESS,
        result=QualityCheck.Result.PASS if passed else QualityCheck.Result.WARN,
        score=report.score,
        anomalies_count=report.missing,
        detail={
            "expected": report.expected,
            "present": report.present,
            "missing": report.missing,
            "threshold": str(threshold),
            "window": window,
        },
    )

    anomalies = [
        Anomaly(
            quality_check=check,
            kind=Anomaly.Kind.MISSING,
            org_unit_id=cell.org_unit_id,
            period=cell.period,
            data_element_id=cell.data_element_id,
            disaggregation=str(cell.coc_id),
            observed_value=None,
            suggested_value=cell.suggested_value,
            suggestion_method=cell.method,
            description=cell.note or f"Valeur manquante ({periods.label(cell.period)})",
        )
        for cell in report.missing_cells
    ]
    Anomaly.objects.bulk_create(anomalies, batch_size=500)
    return check
