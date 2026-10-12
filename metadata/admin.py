"""Interface d'administration des métadonnées (Tranche 1)."""
from django.contrib import admin

from .models import (
    OrgUnit, Category, CategoryOption, CategoryCombo,
    CategoryOptionCombo, DataElement,
)


@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "level", "parent", "path")
    list_filter = ("level",)
    search_fields = ("name", "code")
    autocomplete_fields = ("parent",)
    readonly_fields = ("path", "level", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "code", "parent", "level")}),
        ("Localisation", {"fields": ("path", "opening_date", "geometry")}),
        ("Horodatage", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


class CategoryOptionInline(admin.TabularInline):
    model = CategoryOption
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code")
    search_fields = ("name", "code")
    inlines = [CategoryOptionInline]


@admin.register(CategoryOption)
class CategoryOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category")
    list_filter = ("category",)
    search_fields = ("name", "code")


@admin.register(CategoryCombo)
class CategoryComboAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_default")
    list_filter = ("is_default",)
    search_fields = ("name", "code")
    filter_horizontal = ("categories",)


@admin.register(CategoryOptionCombo)
class CategoryOptionComboAdmin(admin.ModelAdmin):
    list_display = ("name", "combo")
    list_filter = ("combo",)
    search_fields = ("name",)
    filter_horizontal = ("options",)


@admin.register(DataElement)
class DataElementAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "value_type", "aggregation_type", "category_combo")
    list_filter = ("value_type", "aggregation_type", "category_combo")
    search_fields = ("name", "code")
    autocomplete_fields = ("category_combo",)
