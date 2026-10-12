"""
Administration des entités de la couche 1.

Les règles de cohérence se gèrent ici (création, activation, gravité) —
c'est l'interface de configuration voulue : définir soi-même les règles.
"""
from django.contrib import admin

from .models import (
    ImportBatch, BatchVersion, ValidationRule, QualityCheck, Anomaly,
)


class BatchVersionInline(admin.TabularInline):
    model = BatchVersion
    extra = 0
    fields = ("number", "stage", "produced_by", "note", "created_at")
    readonly_fields = ("created_at",)


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("__str__", "sector", "year", "status", "submitted_by", "created_at")
    list_filter = ("sector", "status", "year")
    search_fields = ("label",)
    inlines = [BatchVersionInline]


@admin.register(ValidationRule)
class ValidationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "expression", "level_display", "severity", "is_active")
    list_filter = ("severity", "is_active", "operator")
    search_fields = ("name", "code", "left_code", "right_codes")
    fieldsets = (
        (None, {"fields": ("name", "code", "is_active")}),
        ("Expression", {
            "fields": ("left_code", "operator", "right_codes", "right_factor", "tolerance_pct"),
            "description": "Membre gauche  opérateur  (somme des éléments droite) × facteur. "
                           "Plusieurs codes droite = niveau 2 ; une tolérance > 0 = niveau 3.",
        }),
        ("Signalement", {"fields": ("severity", "message")}),
    )

    @admin.display(description="expression")
    def expression(self, obj):
        ops = {"LE": "≤", "LT": "<", "EQ": "=", "GE": "≥", "GT": ">"}
        right = obj.right_codes.replace(",", " + ")
        factor = "" if obj.right_factor == 1 else f" × {obj.right_factor}"
        tol = "" if not obj.tolerance_pct else f"  (± {obj.tolerance_pct} %)"
        return f"{obj.left_code} {ops.get(obj.operator, obj.operator)} {right}{factor}{tol}"

    @admin.display(description="niveau")
    def level_display(self, obj):
        return f"niveau {obj.level}"


class AnomalyInline(admin.TabularInline):
    model = Anomaly
    extra = 0
    fields = ("kind", "org_unit", "period", "observed_value", "suggested_value", "decision")
    readonly_fields = ("kind",)


@admin.register(QualityCheck)
class QualityCheckAdmin(admin.ModelAdmin):
    list_display = ("version", "step", "result", "score", "anomalies_count", "created_at")
    list_filter = ("step", "result")
    inlines = [AnomalyInline]


@admin.register(Anomaly)
class AnomalyAdmin(admin.ModelAdmin):
    list_display = ("kind", "org_unit", "period", "data_element", "decision", "decided_by")
    list_filter = ("kind", "decision")
    search_fields = ("description", "period")
