"""
Écrans de la couche 1 — fiabilisation
======================================

C1-5a : liste + détail (lecture seule).
C1-5b : créer un lot, lancer les trois contrôles.
C1-5c : traiter les anomalies (décision humaine), signer la version.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from metadata.models import OrgUnit, DataElement
from .models import ImportBatch, BatchVersion, QualityCheck, Anomaly
from . import services


# ---------------------------------------------------------------------------
#  C1-5a — Liste + détail
# ---------------------------------------------------------------------------
@login_required
def batch_list(request):
    batches = (ImportBatch.objects
               .prefetch_related("versions")
               .order_by("-created_at"))
    rows = []
    for b in batches:
        cur = b.current_version
        rows.append({"batch": b, "version": cur, "versions_count": b.versions.count()})
    return render(request, "qualite/batch_list.html", {"rows": rows, "active": "qualite"})


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(
        ImportBatch.objects.prefetch_related("versions__checks"), pk=pk)

    step_order = [QualityCheck.Step.COMPLETENESS,
                  QualityCheck.Step.CONSISTENCY,
                  QualityCheck.Step.OUTLIERS]
    versions = []
    for v in batch.versions.order_by("number"):
        by_step = {c.step: c for c in v.checks.all()}
        versions.append({
            "version": v,
            "checks": [by_step.get(s) for s in step_order],
            "pending": Anomaly.objects.filter(
                quality_check__version=v, decision=Anomaly.Decision.PENDING).count(),
        })

    return render(request, "qualite/batch_detail.html", {
        "batch": batch,
        "versions": versions,
        "steps": ["Complétude", "Cohérence", "Atypiques"],
        "can_sign": batch.status != ImportBatch.Status.VALIDATED,
        "active": "qualite",
    })


# ---------------------------------------------------------------------------
#  C1-5b — Créer un lot
# ---------------------------------------------------------------------------
@login_required
def batch_create(request):
    """Formulaire minimal : secteur + année. Données supposées déjà en base."""
    if request.method == "POST":
        sector = request.POST.get("sector")
        year = request.POST.get("year")
        valid_sectors = {c for c, _ in ImportBatch.Sector.choices}
        errors = []
        if sector not in valid_sectors:
            errors.append("Secteur invalide.")
        try:
            year = int(year)
            if not (2000 <= year <= 2100):
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Année invalide (attendu : 2000–2100).")

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            batch = ImportBatch.objects.create(
                sector=sector, year=year, submitted_by=request.user,
                status=ImportBatch.Status.DRAFT,
            )
            # Version V1 brute, immédiate.
            services.next_version(batch, request.user, BatchVersion.Stage.RAW)
            messages.success(request, f"Lot créé : {batch}. Lance les contrôles quand tu es prêt.")
            return redirect(reverse("qualite:batch_detail", args=[batch.pk]))

    return render(request, "qualite/batch_create.html", {
        "sectors": ImportBatch.Sector.choices,
        "default_year": timezone.now().year,
        "active": "qualite",
    })


# ---------------------------------------------------------------------------
#  C1-5b — Lancer les trois contrôles sur la version courante
# ---------------------------------------------------------------------------
@login_required
def run_checks(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if request.method != "POST":
        return redirect(reverse("qualite:batch_detail", args=[batch.pk]))

    version = batch.current_version
    if version is None:
        version = services.next_version(batch, request.user, BatchVersion.Stage.RAW)

    perimeter = services.perimeter_for(batch)
    if not perimeter["org_unit_ids"] or not perimeter["present_periods"]:
        messages.error(
            request,
            f"Aucune donnée trouvée en base pour l'année {batch.year}. "
            f"Importe d'abord les données, puis relance les contrôles."
        )
        return redirect(reverse("qualite:batch_detail", args=[batch.pk]))

    # On rejoue les contrôles sur la version courante : on efface les contrôles
    # précédents de cette version pour éviter les doublons (ré-exécutable).
    QualityCheck.objects.filter(version=version).delete()

    with transaction.atomic():
        comp, cons, outl = services.run_all_checks(version, perimeter)
        batch.status = ImportBatch.Status.IN_PROGRESS
        batch.save(update_fields=["status"])

    messages.success(
        request,
        f"Contrôles lancés sur V{version.number} : "
        f"complétude {comp.score} % ({comp.anomalies_count} manquante(s)), "
        f"cohérence {cons.anomalies_count} infraction(s), "
        f"atypiques {outl.anomalies_count} valeur(s)."
    )
    return redirect(reverse("qualite:version_anomalies", args=[version.pk]))


# ---------------------------------------------------------------------------
#  C1-5c — Traiter les anomalies d'une version
# ---------------------------------------------------------------------------
@login_required
def version_anomalies(request, pk):
    version = get_object_or_404(
        BatchVersion.objects.select_related("batch"), pk=pk)

    anomalies = (Anomaly.objects
                 .filter(quality_check__version=version)
                 .select_related("org_unit", "data_element", "rule", "quality_check")
                 .order_by("kind", "org_unit__name", "period"))

    counts = {
        "total": anomalies.count(),
        "pending": anomalies.filter(decision=Anomaly.Decision.PENDING).count(),
    }
    return render(request, "qualite/anomalies.html", {
        "version": version,
        "batch": version.batch,
        "anomalies": anomalies,
        "counts": counts,
        "decisions": Anomaly.Decision.choices,
        "active": "qualite",
    })


@login_required
def decide_anomaly(request, pk):
    """Enregistre la décision humaine sur une anomalie."""
    anomaly = get_object_or_404(
        Anomaly.objects.select_related("quality_check__version"), pk=pk)
    if request.method != "POST":
        return redirect(reverse("qualite:version_anomalies",
                                args=[anomaly.quality_check.version_id]))

    decision = request.POST.get("decision")
    valid = {c for c, _ in Anomaly.Decision.choices}
    if decision not in valid:
        messages.error(request, "Décision invalide.")
        return redirect(reverse("qualite:version_anomalies",
                                args=[anomaly.quality_check.version_id]))

    anomaly.decision = decision
    anomaly.decided_by = request.user
    anomaly.justification = request.POST.get("justification", "")[:255]

    # Valeur retenue : saisie libre, sinon la valeur suggérée si « corrigée ».
    raw = request.POST.get("decided_value", "").strip()
    if raw:
        try:
            anomaly.decided_value = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            messages.error(request, "Valeur retenue illisible.")
            return redirect(reverse("qualite:version_anomalies",
                                    args=[anomaly.quality_check.version_id]))
    elif decision == Anomaly.Decision.CORRECTED and anomaly.suggested_value is not None:
        anomaly.decided_value = anomaly.suggested_value

    anomaly.save()
    messages.success(request, "Décision enregistrée.")
    return redirect(reverse("qualite:version_anomalies",
                            args=[anomaly.quality_check.version_id]))


@login_required
def sign_version(request, pk):
    """Signe la version : passe le lot en VALIDÉ (version finale)."""
    version = get_object_or_404(
        BatchVersion.objects.select_related("batch"), pk=pk)
    if request.method != "POST":
        return redirect(reverse("qualite:batch_detail", args=[version.batch_id]))

    pending = Anomaly.objects.filter(
        quality_check__version=version, decision=Anomaly.Decision.PENDING).count()
    if pending:
        messages.error(
            request,
            f"Il reste {pending} anomalie(s) à traiter avant de signer cette version."
        )
        return redirect(reverse("qualite:version_anomalies", args=[version.pk]))

    with transaction.atomic():
        version.stage = BatchVersion.Stage.FINAL
        version.note = (version.note + " · signée")[:255] if version.note else "signée"
        version.save(update_fields=["stage", "note"])
        batch = version.batch
        batch.status = ImportBatch.Status.VALIDATED
        batch.save(update_fields=["status"])

    messages.success(request, f"Version V{version.number} signée. Lot validé.")
    return redirect(reverse("qualite:batch_detail", args=[version.batch_id]))
