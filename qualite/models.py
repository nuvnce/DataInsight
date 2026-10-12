"""
Modèles de la couche 1 — Fiabilisation des données
====================================================

Le socle du pipeline qualité. Cinq entités :

  ImportBatch     le lot d'import : une soumission de données (qui, quand,
                  public/privé, période). C'est l'unité qu'on suit et qu'on signe.
  BatchVersion    une version du lot (V1 brut, V2 complétude corrigée, …).
                  Chaque passage d'étape produit une nouvelle version.
  ValidationRule  une règle de cohérence configurable (grammaire à 3 niveaux).
  QualityCheck    le résultat d'un passage d'une étape sur une version
                  (score, nombre d'anomalies, statut).
  Anomaly         une anomalie détectée, et la décision prise par l'utilisateur.

Principe : rien n'est corrigé automatiquement. Le système détecte et propose ;
l'utilisateur décide ; tout est tracé et historisé.
"""
from django.conf import settings
from django.db import models

from metadata.models import TimeStampedModel, OrgUnit, DataElement


# ---------------------------------------------------------------------------
#  Le lot d'import
# ---------------------------------------------------------------------------
class ImportBatch(TimeStampedModel):
    """Une soumission de données qui traverse le pipeline qualité."""

    class Sector(models.TextChoices):
        PUBLIC = "PUB", "Public (inclut communautaire ASC)"
        PRIVE = "PRV", "Privé"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Déposé"
        IN_PROGRESS = "PROG", "En cours de fiabilisation"
        VALIDATED = "VALID", "Validé (version finale)"
        ARCHIVED = "ARCH", "Archivé"

    label = models.CharField("libellé", max_length=120, blank=True,
                             help_text="Nom lisible du lot, ex. « Public · 2025 ».")
    sector = models.CharField("secteur", max_length=3, choices=Sector.choices)
    year = models.PositiveSmallIntegerField("année couverte")
    status = models.CharField("statut", max_length=6, choices=Status.choices,
                              default=Status.DRAFT)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="batches", verbose_name="déposé par",
    )
    source_kind = models.CharField(
        "origine", max_length=20, default="template",
        help_text="« template » (modèle rempli) ou « dhis2 » (converti).",
    )

    class Meta:
        verbose_name = "lot d'import"
        verbose_name_plural = "lots d'import"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lot #{self.pk} · {self.get_sector_display()} · {self.year}"

    @property
    def current_version(self):
        """La dernière version produite pour ce lot."""
        return self.versions.order_by("-number").first()


# ---------------------------------------------------------------------------
#  Les versions d'un lot
# ---------------------------------------------------------------------------
class BatchVersion(TimeStampedModel):
    """
    Une version signée du lot. V1 = brut déposé ; les suivantes sont produites
    par les corrections apportées à chaque étape.
    """

    class Stage(models.TextChoices):
        RAW = "RAW", "Brut déposé"
        COMPLETENESS = "COMP", "Complétude traitée"
        CONSISTENCY = "CONS", "Cohérence traitée"
        OUTLIERS = "OUTL", "Atypiques traités"
        FINAL = "FINAL", "Version finale validée"

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE,
        related_name="versions", verbose_name="lot",
    )
    number = models.PositiveSmallIntegerField("numéro de version")
    stage = models.CharField("étape", max_length=6, choices=Stage.choices,
                             default=Stage.RAW)
    produced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="produced_versions", verbose_name="produite par",
    )
    file_path = models.CharField(
        "fichier archivé", max_length=255, blank=True,
        help_text="Chemin du fichier .xlsx signé de cette version.",
    )
    note = models.CharField("note", max_length=255, blank=True)

    class Meta:
        verbose_name = "version de lot"
        verbose_name_plural = "versions de lot"
        ordering = ["batch", "number"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "number"],
                                    name="uniq_version_per_batch"),
        ]

    def __str__(self):
        return f"{self.batch} — V{self.number} ({self.get_stage_display()})"


# ---------------------------------------------------------------------------
#  Les règles de cohérence configurables
# ---------------------------------------------------------------------------
class ValidationRule(TimeStampedModel):
    """
    Règle de cohérence à grammaire 3 niveaux :

      Niveau 1 : A  opérateur  B
                 ex. TDR positifs ≤ TDR effectués
      Niveau 2 : A  opérateur  (B + C + … )  × facteur
                 ex. Confirmés = Simple + Grave
      Niveau 3 : idem avec une marge de tolérance (± tolerance_pct %)
                 ex. Σ traités ≈ Confirmés traités  à ± 2 %

    L'expression droite est une liste de codes d'éléments à additionner
    (right_codes), multipliée par un facteur (right_factor).
    """

    class Operator(models.TextChoices):
        LE = "LE", "≤"
        LT = "LT", "<"
        EQ = "EQ", "="
        GE = "GE", "≥"
        GT = "GT", ">"

    class Severity(models.TextChoices):
        BLOCKING = "BLOCK", "Bloquant"
        WARNING = "WARN", "Avertissement"

    name = models.CharField("nom", max_length=150)
    code = models.CharField("code", max_length=50, unique=True)

    # Membre gauche : un élément de données (par code)
    left_code = models.CharField("élément gauche", max_length=50)

    operator = models.CharField("opérateur", max_length=2,
                                choices=Operator.choices)

    # Membre droit : somme d'un ou plusieurs éléments (codes séparés par virgule),
    # multipliée par un facteur. Un seul code = niveau 1 ; plusieurs = niveau 2.
    right_codes = models.CharField(
        "éléments droite", max_length=255,
        help_text="Codes d'éléments à additionner, séparés par des virgules.",
    )
    right_factor = models.DecimalField(
        "facteur", max_digits=8, decimal_places=4, default=1,
        help_text="Multiplicateur appliqué au membre droit (défaut 1).",
    )

    # Niveau 3 : marge de tolérance en %. 0 = pas de tolérance.
    tolerance_pct = models.DecimalField(
        "tolérance (%)", max_digits=5, decimal_places=2, default=0,
        help_text="Marge acceptée autour de l'égalité, en %. 0 = stricte.",
    )

    severity = models.CharField("gravité", max_length=5,
                                choices=Severity.choices,
                                default=Severity.WARNING)
    message = models.CharField("message", max_length=255,
                               help_text="Texte affiché quand la règle est enfreinte.")
    is_active = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "règle de cohérence"
        verbose_name_plural = "règles de cohérence"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def level(self):
        """Niveau de grammaire déduit de la règle (1, 2 ou 3)."""
        codes = [c for c in self.right_codes.split(",") if c.strip()]
        if self.tolerance_pct and self.tolerance_pct > 0:
            return 3
        return 2 if len(codes) > 1 else 1


# ---------------------------------------------------------------------------
#  Résultat d'un contrôle sur une version
# ---------------------------------------------------------------------------
class QualityCheck(TimeStampedModel):
    """Résultat du passage d'une étape (complétude/cohérence/atypiques)."""

    class Step(models.TextChoices):
        COMPLETENESS = "COMP", "Complétude"
        CONSISTENCY = "CONS", "Cohérence"
        OUTLIERS = "OUTL", "Valeurs atypiques"

    class Result(models.TextChoices):
        PASS = "PASS", "Conforme"
        WARN = "WARN", "Avertissements"
        FAIL = "FAIL", "Anomalies bloquantes"

    version = models.ForeignKey(
        BatchVersion, on_delete=models.CASCADE,
        related_name="checks", verbose_name="version",
    )
    step = models.CharField("étape", max_length=4, choices=Step.choices)
    result = models.CharField("résultat", max_length=4, choices=Result.choices)
    score = models.DecimalField(
        "score", max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Pour la complétude : % de remplissage.",
    )
    anomalies_count = models.PositiveIntegerField("nombre d'anomalies", default=0)
    detail = models.JSONField("détail", default=dict, blank=True,
                              help_text="Résumé chiffré du contrôle.")

    class Meta:
        verbose_name = "contrôle qualité"
        verbose_name_plural = "contrôles qualité"
        ordering = ["version", "step"]

    def __str__(self):
        return f"{self.version} — {self.get_step_display()} : {self.get_result_display()}"


# ---------------------------------------------------------------------------
#  Une anomalie détectée + la décision prise
# ---------------------------------------------------------------------------
class Anomaly(TimeStampedModel):
    """
    Une anomalie repérée par un contrôle, et la décision de l'utilisateur.
    Trace complète : quelle cellule, quelle valeur, quelle action, qui, quand.
    """

    class Kind(models.TextChoices):
        MISSING = "MISS", "Valeur manquante"
        INCONSISTENT = "INCO", "Incohérence"
        OUTLIER = "OUTL", "Valeur atypique"

    class Decision(models.TextChoices):
        PENDING = "PEND", "À traiter"
        CORRECTED = "CORR", "Corrigée"
        TO_VERIFY = "VERIF", "À vérifier"
        ACCEPTED = "ACCEPT", "Acceptée avec justification"

    quality_check = models.ForeignKey(
        QualityCheck, on_delete=models.CASCADE,
        related_name="anomalies", verbose_name="contrôle",
    )
    kind = models.CharField("type", max_length=4, choices=Kind.choices)

    # Localisation de la cellule concernée
    org_unit = models.ForeignKey(
        OrgUnit, on_delete=models.PROTECT, null=True, blank=True,
        related_name="anomalies", verbose_name="unité d'organisation",
    )
    period = models.CharField("période", max_length=10, blank=True)
    data_element = models.ForeignKey(
        DataElement, on_delete=models.PROTECT, null=True, blank=True,
        related_name="anomalies", verbose_name="élément de données",
    )
    disaggregation = models.CharField("désagrégation", max_length=120, blank=True)

    rule = models.ForeignKey(
        ValidationRule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="anomalies", verbose_name="règle enfreinte",
    )

    observed_value = models.DecimalField(
        "valeur observée", max_digits=15, decimal_places=2, null=True, blank=True)
    suggested_value = models.DecimalField(
        "valeur suggérée", max_digits=15, decimal_places=2, null=True, blank=True)
    suggestion_method = models.CharField(
        "méthode de suggestion", max_length=40, blank=True,
        help_text="ex. « moyenne mobile », « médiane », « z-score modifié ».",
    )
    description = models.CharField("description", max_length=255, blank=True)

    # Décision de l'utilisateur
    decision = models.CharField("décision", max_length=6,
                                choices=Decision.choices,
                                default=Decision.PENDING)
    decided_value = models.DecimalField(
        "valeur retenue", max_digits=15, decimal_places=2, null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="decided_anomalies", verbose_name="décidée par",
    )
    justification = models.CharField("justification", max_length=255, blank=True)

    class Meta:
        verbose_name = "anomalie"
        verbose_name_plural = "anomalies"
        ordering = ["quality_check", "org_unit", "period"]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.description or self.pk}"
