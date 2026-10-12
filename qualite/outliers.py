"""
Moteur des valeurs atypiques — couche 1, contrôle 3/3
======================================================

Troisième et dernière porte de fiabilisation. Elle repère les valeurs qui
sortent du comportement attendu d'une série, par deux regards croisés :

  1. **Z-score modifié (MAD)** — robuste aux extrêmes. Pour une série, on
     calcule la médiane et l'écart absolu médian (MAD) ; une valeur est atypique
     si son z-score modifié dépasse un seuil (3,5 par défaut, valeur usuelle).

         z = 0,6745 × (x − médiane) / MAD

  2. **Comparaison saisonnière** — on compare la valeur à la même période des
     années voisines. Un écart relatif important (seuil % configurable) la
     signale aussi. Essentiel en paludisme : la saisonnalité est forte, un pic
     de saison n'est pas une anomalie.

Une valeur signalée par **l'un ou l'autre** regard devient une `Anomaly` (type
OUTL), avec une **valeur de remplacement proposée** (la médiane de la série, ou
la valeur saisonnière voisine). Comme toute la couche 1 : on détecte et on
propose, l'humain décide ; aucune `DataValue` n'est modifiée.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal

from collecte import periods
from collecte.models import DataValue


DEFAULT_MAD_THRESHOLD = Decimal("3.5")      # seuil z-score modifié (usuel)
DEFAULT_SEASONAL_PCT = Decimal("50")        # écart saisonnier relatif, en %
_CONSISTENCY_CONST = Decimal("0.6745")      # constante du z-score modifié


# ---------------------------------------------------------------------------
#  Calculs purs (sur une série période -> valeur), testables sans base
# ---------------------------------------------------------------------------
def modified_zscore(series: dict[str, Decimal], target: str) -> Decimal | None:
    """
    Z-score modifié de la valeur en `target`, basé médiane + MAD de la série.
    None si la valeur cible est absente ou la série trop courte (< 3 points).
    """
    if target not in series:
        return None
    vals = list(series.values())
    if len(vals) < 3:
        return None

    med = Decimal(str(statistics.median(vals)))
    abs_dev = [abs(v - med) for v in vals]
    mad = Decimal(str(statistics.median(abs_dev)))

    x = series[target]
    if mad == 0:
        # série quasi constante : atypique seulement si la valeur s'en écarte
        return Decimal("0") if x == med else None
    return (_CONSISTENCY_CONST * (x - med) / mad).quantize(Decimal("0.01"))


def seasonal_gap(series: dict[str, Decimal], target: str) -> tuple[Decimal | None, Decimal | None]:
    """
    Compare la valeur cible à la même période des années voisines.
    Renvoie (écart_relatif_en_%, valeur_de_référence_voisine), ou (None, None)
    si pas de voisin saisonnier exploitable.
    """
    if target not in series:
        return None, None
    try:
        ptype = periods.detect_type(target)
    except periods.InvalidPeriod:
        return None, None
    if ptype == periods.YEARLY:
        return None, None

    def shift_year(code: str, delta: int) -> str:
        if ptype == periods.MONTHLY:
            return f"{int(code[:4]) + delta}{code[4:6]}"
        y, q = code.split("Q")
        return f"{int(y) + delta}Q{q}"

    neighbors = [series[c] for d in (-1, 1)
                 if (c := shift_year(target, d)) in series]
    if not neighbors:
        return None, None

    ref = Decimal(str(statistics.mean(neighbors)))
    x = series[target]
    if ref == 0:
        gap = Decimal("0") if x == 0 else Decimal("100")
    else:
        gap = (abs(x - ref) / ref * 100).quantize(Decimal("0.01"))
    return gap, ref.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
#  Résultats
# ---------------------------------------------------------------------------
@dataclass
class OutlierCell:
    org_unit_id: int
    period: str
    data_element_id: int
    coc_id: int
    observed: Decimal
    zscore: Decimal | None = None
    seasonal_pct: Decimal | None = None
    suggested_value: Decimal | None = None
    method: str = ""
    reason: str = ""


@dataclass
class OutlierReport:
    values_examined: int = 0
    cells: list[OutlierCell] = field(default_factory=list)

    @property
    def outliers_count(self) -> int:
        return len(self.cells)


# ---------------------------------------------------------------------------
#  Analyse d'un périmètre
# ---------------------------------------------------------------------------
def analyze(org_unit_ids, periods_list, data_element_ids, coc_ids,
            mad_threshold: Decimal = DEFAULT_MAD_THRESHOLD,
            seasonal_pct: Decimal = DEFAULT_SEASONAL_PCT) -> OutlierReport:
    """
    Pour chaque série (unité × élément × désagrégation), teste chaque valeur
    présente dans le périmètre : atypique si |z modifié| > seuil, OU si l'écart
    saisonnier relatif dépasse le seuil %.
    """
    org_unit_ids = list(org_unit_ids)
    periods_set = set(periods_list)
    data_element_ids = list(data_element_ids)
    coc_ids = list(coc_ids)

    report = OutlierReport()
    if not (org_unit_ids and periods_set and data_element_ids and coc_ids):
        return report

    # On charge TOUTES les périodes connues de ces séries (pas seulement celles
    # du périmètre), car le z-score et le saisonnier ont besoin de l'historique.
    rows = DataValue.objects.filter(
        org_unit_id__in=org_unit_ids,
        data_element_id__in=data_element_ids,
        category_option_combo_id__in=coc_ids,
    ).values_list("org_unit_id", "period", "data_element_id",
                  "category_option_combo_id", "value")

    series_map: dict[tuple, dict[str, Decimal]] = {}
    for ou, per, de, coc, val in rows:
        series_map.setdefault((ou, de, coc), {})[per] = val

    for (ou, de, coc), serie in series_map.items():
        for per, x in serie.items():
            if per not in periods_set:
                continue    # on ne signale que les valeurs du périmètre demandé
            report.values_examined += 1

            z = modified_zscore(serie, per)
            gap, ref = seasonal_gap(serie, per)

            flagged_z = z is not None and abs(z) > mad_threshold
            flagged_s = gap is not None and gap > seasonal_pct
            if not (flagged_z or flagged_s):
                continue

            # Valeur proposée : la référence saisonnière si elle a déclenché,
            # sinon la médiane de la série (remplacement robuste).
            if flagged_s and ref is not None:
                suggested, method = ref, "comparaison saisonnière"
            else:
                med = Decimal(str(statistics.median(list(serie.values()))))
                suggested, method = med.quantize(Decimal("0.01")), "médiane de la série"

            reasons = []
            if flagged_z:
                reasons.append(f"z modifié = {z}")
            if flagged_s:
                reasons.append(f"écart saisonnier {gap}%")

            report.cells.append(OutlierCell(
                org_unit_id=ou, period=per, data_element_id=de, coc_id=coc,
                observed=x, zscore=z, seasonal_pct=gap,
                suggested_value=suggested, method=method,
                reason=" ; ".join(reasons),
            ))
    return report


# ---------------------------------------------------------------------------
#  Persistance
# ---------------------------------------------------------------------------
def run_and_record(version, org_unit_ids, periods_list, data_element_ids, coc_ids,
                   mad_threshold: Decimal = DEFAULT_MAD_THRESHOLD,
                   seasonal_pct: Decimal = DEFAULT_SEASONAL_PCT):
    """
    Lance l'analyse sur une `BatchVersion` et enregistre : un `QualityCheck`
    (étape atypiques) + une `Anomaly` (type OUTL) par valeur signalée, avec sa
    valeur de remplacement proposée. Ne modifie aucune `DataValue`.
    """
    from .models import QualityCheck, Anomaly

    report = analyze(org_unit_ids, periods_list, data_element_ids, coc_ids,
                     mad_threshold, seasonal_pct)

    check = QualityCheck.objects.create(
        version=version,
        step=QualityCheck.Step.OUTLIERS,
        result=QualityCheck.Result.PASS if report.outliers_count == 0
               else QualityCheck.Result.WARN,
        anomalies_count=report.outliers_count,
        detail={
            "values_examined": report.values_examined,
            "outliers": report.outliers_count,
            "mad_threshold": str(mad_threshold),
            "seasonal_pct": str(seasonal_pct),
        },
    )

    anomalies = [
        Anomaly(
            quality_check=check,
            kind=Anomaly.Kind.OUTLIER,
            org_unit_id=c.org_unit_id,
            period=c.period,
            data_element_id=c.data_element_id,
            disaggregation=str(c.coc_id),
            observed_value=c.observed,
            suggested_value=c.suggested_value,
            suggestion_method=c.method,
            description=f"Valeur atypique ({c.reason})"[:255],
        )
        for c in report.cells
    ]
    Anomaly.objects.bulk_create(anomalies, batch_size=500)
    return check
