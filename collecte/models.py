"""
Modèles de collecte — Tranche 2.

Table centrale du système : DataValue. Chaque valeur numérique est identifiée
par le quadruplet (élément de données, unité d'organisation, période,
combinaison de désagrégation), sous contrainte d'unicité. C'est ce qui garantit
l'intégrité et rendra les imports Excel idempotents.
"""
from django.core.exceptions import ValidationError
from django.db import models

from metadata.models import (
    TimeStampedModel, OrgUnit, DataElement, CategoryOptionCombo,
)
from . import periods


class DataSet(TimeStampedModel):
    """
    Formulaire de collecte : regroupe des éléments de données, une fréquence
    de rapportage (type de période) et les unités d'organisation qui le
    renseignent. Correspond au `data_set` du MLD, avec ses deux liaisons
    (éléments et unités) exprimées en ManyToMany.
    """
    PERIOD_TYPE_CHOICES = [
        (periods.MONTHLY, "Mensuel"),
        (periods.QUARTERLY, "Trimestriel"),
        (periods.YEARLY, "Annuel"),
    ]

    name = models.CharField("nom", max_length=200)
    code = models.CharField("code", max_length=50, unique=True)
    period_type = models.CharField(
        "type de période", max_length=10,
        choices=PERIOD_TYPE_CHOICES, default=periods.MONTHLY,
    )
    data_elements = models.ManyToManyField(
        DataElement, related_name="data_sets", verbose_name="éléments de données", blank=True,
    )
    org_units = models.ManyToManyField(
        OrgUnit, related_name="data_sets", verbose_name="unités d'organisation", blank=True,
    )
    description = models.TextField("description", blank=True)

    class Meta:
        verbose_name = "formulaire de collecte"
        verbose_name_plural = "formulaires de collecte"
        ordering = ["name"]

    def __str__(self):
        return self.name


class DataValue(TimeStampedModel):
    """Une valeur rapportée pour un élément, une unité, une période, une désagrégation."""

    data_element = models.ForeignKey(
        DataElement, on_delete=models.PROTECT,
        related_name="values", verbose_name="élément de données",
    )
    org_unit = models.ForeignKey(
        OrgUnit, on_delete=models.PROTECT,
        related_name="values", verbose_name="unité d'organisation",
    )
    period = models.CharField("période", max_length=10)
    category_option_combo = models.ForeignKey(
        CategoryOptionCombo, on_delete=models.PROTECT,
        related_name="values", verbose_name="désagrégation",
    )
    value = models.DecimalField("valeur", max_digits=15, decimal_places=2)
    comment = models.CharField("commentaire", max_length=255, blank=True)

    class Meta:
        verbose_name = "valeur"
        verbose_name_plural = "valeurs"
        ordering = ["-period", "org_unit", "data_element"]
        constraints = [
            models.UniqueConstraint(
                fields=["data_element", "org_unit", "period", "category_option_combo"],
                name="uniq_data_value",
            )
        ]
        indexes = [
            models.Index(fields=["period"]),
            models.Index(fields=["org_unit", "period"]),
            models.Index(fields=["data_element", "period"]),
        ]

    def __str__(self):
        return f"{self.data_element.code}@{self.org_unit.code}/{self.period} = {self.value}"

    def clean(self):
        # Période valide
        try:
            periods.validate(self.period)
        except periods.InvalidPeriod as e:
            raise ValidationError({"period": str(e)})

        # La désagrégation doit appartenir au combo de l'élément
        if self.data_element_id and self.category_option_combo_id:
            expected_combo_id = self.data_element.category_combo_id
            actual_combo_id = self.category_option_combo.combo_id
            if expected_combo_id != actual_combo_id:
                raise ValidationError({
                    "category_option_combo":
                        "Cette désagrégation n'appartient pas à la combinaison "
                        "de catégories de l'élément de données."
                })

        # Type de valeur : entiers positifs
        if self.value is not None and self.data_element_id:
            vt = self.data_element.value_type
            if vt in (DataElement.ValueType.POSITIVE_INTEGER,) and self.value < 0:
                raise ValidationError({"value": "La valeur doit être positive."})
            if vt in (DataElement.ValueType.INTEGER,
                      DataElement.ValueType.POSITIVE_INTEGER):
                if self.value != self.value.to_integral_value():
                    raise ValidationError({"value": "La valeur doit être un entier."})

    @property
    def period_label(self):
        try:
            return periods.label(self.period)
        except periods.InvalidPeriod:
            return self.period


class Indicator(TimeStampedModel):
    """
    Indicateur dérivé : ratio numérateur / dénominateur × facteur.
    Numérateur et dénominateur référencent des éléments de données par leur code.
    Ex. Taux de positivité TDR = TDR_POS / TDR_FAIT × 100.

    Les expressions sont, pour l'instant, de simples codes d'éléments (un seul
    par numérateur/dénominateur). On pourra enrichir vers des sommes plus tard.
    """
    name = models.CharField("nom", max_length=200)
    code = models.CharField("code", max_length=50, unique=True)
    numerator_code = models.CharField(
        "code élément numérateur", max_length=50,
        help_text="Code de l'élément de données au numérateur (ex. TDR_POS).",
    )
    denominator_code = models.CharField(
        "code élément dénominateur", max_length=50,
        help_text="Code de l'élément au dénominateur (ex. TDR_FAIT).",
    )
    factor = models.DecimalField("facteur", max_digits=10, decimal_places=2, default=100)
    description = models.TextField("description", blank=True)

    class Meta:
        verbose_name = "indicateur dérivé"
        verbose_name_plural = "indicateurs dérivés"
        ordering = ["name"]

    def __str__(self):
        return self.name

# ---------------------------------------------------------------------------
#  Population (donnée de référence — brique 1)
# ---------------------------------------------------------------------------
class Population(TimeStampedModel):
    """Population de référence : district × année × (sexe) × (tranche d'âge)."""

    class Sex(models.TextChoices):
        MALE = "M", "Masculin"
        FEMALE = "F", "Féminin"
        # absence de valeur (NULL) = les deux sexes confondus (ensemble)

    class AgeGroup(models.TextChoices):
        U5 = "U5", "Moins de 5 ans"
        O5 = "O5", "5 ans et plus"
        FE = "FE", "Femme enceinte"
        # absence de valeur (NULL) = tous âges confondus (total)

    org_unit = models.ForeignKey(
        OrgUnit, on_delete=models.PROTECT,
        related_name="populations", verbose_name="unité d'organisation (district)",
    )
    year = models.PositiveSmallIntegerField("année")  # ex. 2024
    sex = models.CharField(
        "sexe", max_length=1, choices=Sex.choices,
        null=True, blank=True,
        help_text="Vide = les deux sexes confondus.",
    )
    age_group = models.CharField(
        "tranche d'âge", max_length=4, choices=AgeGroup.choices,
        null=True, blank=True,
        help_text="Vide = tous âges confondus.",
    )
    value = models.PositiveIntegerField("effectif")
    source = models.CharField(
        "source", max_length=100, blank=True,
        help_text="Origine de la donnée (ex. « RGPH5 projection »).",
    )

    class Meta:
        verbose_name = "population"
        verbose_name_plural = "populations"
        ordering = ["org_unit", "year", "sex", "age_group"]
        constraints = [
            models.UniqueConstraint(
                fields=["org_unit", "year", "sex", "age_group"],
                name="uniq_population",
            )
        ]
        indexes = [
            models.Index(fields=["org_unit", "year"]),
            models.Index(fields=["year"]),
        ]

    def __str__(self):
        strat = []
        if self.sex:
            strat.append(self.get_sex_display())
        if self.age_group:
            strat.append(self.get_age_group_display())
        detail = f" [{', '.join(strat)}]" if strat else ""
        return f"{self.org_unit.name} — {self.year}{detail} : {self.value}"
