"""
Moteur de cohérence — couche 1, contrôle 2/3
=============================================

Deuxième porte de fiabilisation. Elle vérifie que les valeurs se tiennent entre
elles, selon des règles configurables (la grammaire à 3 niveaux de
`ValidationRule`) :

    Niveau 1 : A  op  B                      ex. TDR positifs ≤ TDR effectués
    Niveau 2 : A  op  (B + C + …) × facteur   ex. Confirmés = Simple + Grave
    Niveau 3 : idem, avec tolérance ± t %     ex. Σ traités ≈ Confirmés  à ± 2 %

Principe de la couche 1, inchangé : le moteur *détecte et signale*. Chaque
infraction devient une `Anomaly` (type INCO). Rien n'est corrigé ; aucune
`DataValue` n'est modifiée.

Évaluation désagrégée, regroupement à l'affichage
-------------------------------------------------
Une règle s'évalue **par cellule** : une combinaison (unité × période ×
désagrégation). On compare les codes d'éléments *à l'intérieur de la même
cellule*. Le périmètre (quelles unités, quelles périodes) est fourni par
l'appelant, comme pour la complétude.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from collecte import periods
from collecte.models import DataValue


# opérateurs -> fonction de comparaison (gauche, droite) -> conforme ?
def _cmp(op: str, left: Decimal, right: Decimal, tol: Decimal) -> bool:
    """
    Teste la relation `left op right`. Pour l'égalité, la tolérance (en %) est
    appliquée autour du membre droit : |gauche − droite| ≤ droite × t/100.
    """
    if op == "LE":
        return left <= right
    if op == "LT":
        return left < right
    if op == "GE":
        return left >= right
    if op == "GT":
        return left > right
    # EQ : égalité à la tolérance près
    if tol and tol > 0:
        margin = abs(right) * tol / Decimal("100")
        return abs(left - right) <= margin
    return left == right


_OP_SYMBOL = {"LE": "≤", "LT": "<", "EQ": "=", "GE": "≥", "GT": ">"}


@dataclass
class Violation:
    """Une infraction à une règle, localisée sur une cellule."""
    rule_id: int
    rule_code: str
    severity: str
    org_unit_id: int
    period: str
    coc_id: int
    left_value: Decimal
    right_value: Decimal
    message: str = ""


@dataclass
class ConsistencyReport:
    """Bilan d'un passage de cohérence."""
    cells_tested: int = 0            # nb de (règle × cellule) effectivement évaluées
    violations: list[Violation] = field(default_factory=list)
    skipped_missing: int = 0         # cellules sautées faute de données présentes

    @property
    def violations_count(self) -> int:
        return len(self.violations)

    @property
    def blocking_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "BLOCK")


# ---------------------------------------------------------------------------
#  Évaluation
# ---------------------------------------------------------------------------
def _right_codes(rule) -> list[str]:
    return [c.strip() for c in rule.right_codes.split(",") if c.strip()]


def analyze(rules, org_unit_ids, periods_list, coc_ids) -> ConsistencyReport:
    """
    Évalue les règles fournies (déjà filtrées sur `is_active` par l'appelant,
    ou non) sur le périmètre (unités × périodes × désagrégations).

    Une cellule n'est évaluée pour une règle que si **tous** les codes
    nécessaires (gauche + droite) y sont présents. Sinon la cellule est comptée
    comme sautée (c'est le rôle de la complétude de signaler les trous, pas
    celui de la cohérence).
    """
    rules = list(rules)
    org_unit_ids = list(org_unit_ids)
    periods_list = sorted(set(periods_list))
    coc_ids = list(coc_ids)

    report = ConsistencyReport()
    if not rules or not org_unit_ids or not periods_list or not coc_ids:
        return report

    # Tous les codes d'éléments touchés par l'ensemble des règles.
    needed_codes: set[str] = set()
    for r in rules:
        needed_codes.add(r.left_code.strip())
        needed_codes.update(_right_codes(r))

    # Une seule requête : toutes les valeurs du périmètre pour ces codes.
    rows = DataValue.objects.filter(
        org_unit_id__in=org_unit_ids,
        period__in=periods_list,
        category_option_combo_id__in=coc_ids,
        data_element__code__in=needed_codes,
    ).values_list("org_unit_id", "period", "category_option_combo_id",
                  "data_element__code", "value")

    # Index en mémoire : (ou, per, coc) -> { code: valeur }
    cells: dict[tuple, dict[str, Decimal]] = {}
    for ou, per, coc, code, val in rows:
        cells.setdefault((ou, per, coc), {})[code] = val

    for (ou, per, coc), values in cells.items():
        for rule in rules:
            left_code = rule.left_code.strip()
            rcodes = _right_codes(rule)

            # Toutes les composantes présentes ?
            if left_code not in values or any(c not in values for c in rcodes):
                report.skipped_missing += 1
                continue

            left = values[left_code]
            right = sum((values[c] for c in rcodes), Decimal("0")) * rule.right_factor
            report.cells_tested += 1

            if not _cmp(rule.operator, left, right, rule.tolerance_pct):
                sym = _OP_SYMBOL.get(rule.operator, rule.operator)
                msg = rule.message or (
                    f"{left_code}={left} {sym} {'+'.join(rcodes)}={right} non respecté"
                )
                report.violations.append(Violation(
                    rule_id=rule.id, rule_code=rule.code, severity=rule.severity,
                    org_unit_id=ou, period=per, coc_id=coc,
                    left_value=left, right_value=right, message=msg,
                ))
    return report


# ---------------------------------------------------------------------------
#  Persistance
# ---------------------------------------------------------------------------
def run_and_record(version, org_unit_ids, periods_list, coc_ids, rules=None):
    """
    Lance l'analyse sur une `BatchVersion` et enregistre le résultat : un
    `QualityCheck` (étape cohérence) + une `Anomaly` (type INCO) par infraction.

    `rules` : itérable de `ValidationRule`. Par défaut, toutes les règles
    actives. Ne modifie aucune `DataValue`.
    """
    from .models import QualityCheck, Anomaly, ValidationRule

    if rules is None:
        rules = ValidationRule.objects.filter(is_active=True)
    rules = list(rules)

    report = analyze(rules, org_unit_ids, periods_list, coc_ids)

    if report.blocking_count:
        result = QualityCheck.Result.FAIL
    elif report.violations_count:
        result = QualityCheck.Result.WARN
    else:
        result = QualityCheck.Result.PASS

    check = QualityCheck.objects.create(
        version=version,
        step=QualityCheck.Step.CONSISTENCY,
        result=result,
        anomalies_count=report.violations_count,
        detail={
            "rules_evaluated": len(rules),
            "cells_tested": report.cells_tested,
            "violations": report.violations_count,
            "blocking": report.blocking_count,
            "skipped_missing": report.skipped_missing,
        },
    )

    anomalies = [
        Anomaly(
            quality_check=check,
            kind=Anomaly.Kind.INCONSISTENT,
            org_unit_id=v.org_unit_id,
            period=v.period,
            disaggregation=str(v.coc_id),
            rule_id=v.rule_id,
            observed_value=v.left_value,
            suggested_value=None,   # la cohérence signale, ne propose pas de valeur
            description=v.message[:255],
        )
        for v in report.violations
    ]
    Anomaly.objects.bulk_create(anomalies, batch_size=500)
    return check
