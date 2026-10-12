"""
Modèles de métadonnées — Tranche 1 (socle).

Ce module définit les métadonnées structurantes du système :
  - Unité d'organisation (hiérarchie géographique + géométrie)
  - Catégories de désagrégation et leurs options / combinaisons
  - Élément de données (variable mesurée)

Note géospatiale
----------------
Le champ géométrique de l'unité d'organisation n'est réellement exploité
qu'à la tranche « cartographie ». Pour que le projet tourne dans tout
environnement (y compris sans GDAL/PostGIS), la géométrie est stockée ici
sous forme GeoJSON (texte) par défaut. En production, on bascule vers un
vrai champ PostGIS `MultiPolygonField` (voir le commentaire plus bas).
La logique métier de la tranche 1 est identique dans les deux cas.
"""
from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    """Base : horodatage de création et de mise à jour."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="modifié le")

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
#  Unité d'organisation
# ---------------------------------------------------------------------------
class OrgUnit(TimeStampedModel):
    """
    Unité d'organisation : maille géographique/administrative hiérarchique.
    Ex. Pays > Région > District > Formation sanitaire.

    La hiérarchie est auto-référencée (parent). Le champ `path` matérialise
    le chemin racine->noeud (ex. « /1/4/12/ ») pour des requêtes de sous-arbre
    efficaces ; il est recalculé automatiquement à l'enregistrement.
    """
    name = models.CharField("nom", max_length=200)
    code = models.CharField("code", max_length=50, unique=True)
    level = models.PositiveSmallIntegerField("niveau", default=1)
    parent = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.PROTECT, related_name="children",
        verbose_name="unité parente",
    )
    path = models.CharField("chemin", max_length=255, editable=False, db_index=True, default="/")
    opening_date = models.DateField("date d'ouverture", null=True, blank=True)

    # Géométrie : GeoJSON en texte par défaut (portable partout).
    # En production PostGIS, remplacer par :
    #     from django.contrib.gis.db import models as gis
    #     geometry = gis.MultiPolygonField(srid=4326, null=True, blank=True)
    geometry = models.TextField(
        "géométrie (GeoJSON)", null=True, blank=True,
        help_text="Limites de l'unité au format GeoJSON. En production, champ PostGIS.",
    )

    class Meta:
        verbose_name = "unité d'organisation"
        verbose_name_plural = "unités d'organisation"
        ordering = ["level", "name"]
        indexes = [models.Index(fields=["level"]), models.Index(fields=["parent"])]

    def __str__(self):
        return f"{self.name} (niv. {self.level})"

    def clean(self):
        if self.parent is None:
            if self.level != 1:
                raise ValidationError({"level": "Une unité racine doit être de niveau 1."})
        else:
            if self.parent_id == self.pk and self.pk is not None:
                raise ValidationError({"parent": "Une unité ne peut pas être son propre parent."})
            expected = self.parent.level + 1
            if self.level != expected:
                raise ValidationError(
                    {"level": f"Le niveau doit être {expected} (parent de niveau {self.parent.level})."}
                )

    def _compute_path(self):
        if self.parent is None:
            return f"/{self.pk}/"
        return f"{self.parent.path}{self.pk}/"

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if self.parent is not None:
            self.level = self.parent.level + 1
        super().save(*args, **kwargs)
        new_path = self._compute_path()
        if self.path != new_path:
            self.path = new_path
            super().save(update_fields=["path"])

    @property
    def is_leaf(self):
        return not self.children.exists()

    def ancestors(self):
        """Liste des unités ancêtres (racine -> parent direct)."""
        ids = [int(x) for x in self.path.strip("/").split("/") if x][:-1]
        return list(OrgUnit.objects.filter(pk__in=ids).order_by("level"))


# ---------------------------------------------------------------------------
#  Désagrégation : catégories, options, combinaisons
# ---------------------------------------------------------------------------
class Category(TimeStampedModel):
    """Dimension de désagrégation. Ex. « Sexe », « Tranche d'âge »."""
    name = models.CharField("nom", max_length=100, unique=True)
    code = models.CharField("code", max_length=50, unique=True)

    class Meta:
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CategoryOption(TimeStampedModel):
    """Modalité d'une catégorie. Ex. « Masculin », « Moins de 5 ans »."""
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="options", verbose_name="catégorie"
    )
    name = models.CharField("nom", max_length=100)
    code = models.CharField("code", max_length=50)

    class Meta:
        verbose_name = "option de catégorie"
        verbose_name_plural = "options de catégorie"
        ordering = ["category", "name"]
        constraints = [
            models.UniqueConstraint(fields=["category", "code"], name="uniq_option_code_per_category"),
        ]

    def __str__(self):
        return f"{self.category.name} : {self.name}"


class CategoryCombo(TimeStampedModel):
    """
    Regroupement de catégories appliqué à un élément de données.
    Un combo « par défaut » (sans catégorie) sert aux éléments non désagrégés.
    """
    name = models.CharField("nom", max_length=150, unique=True)
    code = models.CharField("code", max_length=50, unique=True)
    categories = models.ManyToManyField(
        Category, blank=True, related_name="combos", verbose_name="catégories"
    )
    is_default = models.BooleanField("combinaison par défaut", default=False)

    class Meta:
        verbose_name = "combinaison de catégories"
        verbose_name_plural = "combinaisons de catégories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CategoryOptionCombo(TimeStampedModel):
    """
    Combinaison concrète d'options servant de clé sur les valeurs.
    Ex. « Masculin, Moins de 5 ans ». Pour un combo par défaut, une seule
    combinaison « (défaut) » existe.
    """
    combo = models.ForeignKey(
        CategoryCombo, on_delete=models.CASCADE,
        related_name="option_combos", verbose_name="combinaison de catégories",
    )
    name = models.CharField("nom", max_length=200)
    options = models.ManyToManyField(
        CategoryOption, blank=True, related_name="option_combos", verbose_name="options",
    )

    class Meta:
        verbose_name = "combinaison de désagrégation"
        verbose_name_plural = "combinaisons de désagrégation"
        ordering = ["combo", "name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
#  Élément de données
# ---------------------------------------------------------------------------
class DataElement(TimeStampedModel):
    """
    Variable mesurée. Ex. « Cas confirmés », « TDR réalisés ».
    """
    class ValueType(models.TextChoices):
        INTEGER = "INTEGER", "Entier"
        POSITIVE_INTEGER = "POSITIVE_INTEGER", "Entier positif"
        NUMBER = "NUMBER", "Nombre décimal"

    class AggregationType(models.TextChoices):
        SUM = "SUM", "Somme"
        AVERAGE = "AVERAGE", "Moyenne"
        COUNT = "COUNT", "Décompte"
        NONE = "NONE", "Aucune"

    name = models.CharField("nom", max_length=200)
    code = models.CharField("code", max_length=50, unique=True)
    value_type = models.CharField(
        "type de valeur", max_length=20,
        choices=ValueType.choices, default=ValueType.POSITIVE_INTEGER,
    )
    aggregation_type = models.CharField(
        "type d'agrégation", max_length=10,
        choices=AggregationType.choices, default=AggregationType.SUM,
    )
    category_combo = models.ForeignKey(
        CategoryCombo, on_delete=models.PROTECT,
        related_name="data_elements", verbose_name="combinaison de catégories",
    )
    description = models.TextField("description", blank=True)

    class Meta:
        verbose_name = "élément de données"
        verbose_name_plural = "éléments de données"
        ordering = ["name"]

    def __str__(self):
        return self.name
