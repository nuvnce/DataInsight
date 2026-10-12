"""Administration de la collecte (Tranches 2-3)."""
from django.contrib import admin

from .models import DataValue, DataSet


@admin.register(DataSet)
class DataSetAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "period_type", "nb_elements", "nb_org_units")
    list_filter = ("period_type",)
    search_fields = ("name", "code")
    filter_horizontal = ("data_elements", "org_units")

    @admin.display(description="éléments")
    def nb_elements(self, obj):
        return obj.data_elements.count()

    @admin.display(description="unités")
    def nb_org_units(self, obj):
        return obj.org_units.count()


@admin.register(DataValue)
class DataValueAdmin(admin.ModelAdmin):
    list_display = ("data_element", "org_unit", "period_label", "category_option_combo", "value")
    list_filter = ("period", "org_unit", "data_element")
    search_fields = ("data_element__name", "org_unit__name")
    autocomplete_fields = ("data_element", "org_unit", "category_option_combo")

    @admin.display(description="période")
    def period_label(self, obj):
        return obj.period_label


from .models import Indicator

@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "numerator_code", "denominator_code", "factor")
    search_fields = ("name", "code")
