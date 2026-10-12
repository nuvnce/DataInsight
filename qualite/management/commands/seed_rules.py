"""
seed_rules — pré-remplit les règles de cohérence de départ
===========================================================

Installe un jeu de 7 règles couvrant les trois niveaux de grammaire, sur les
quatre familles d'indicateurs (confirmés, décès, hospitalisés, testés).

Idempotent : relançable sans créer de doublon (clé = `code`).

Les règles se modifient ensuite dans l'admin (activer/désactiver, changer la
gravité, ajuster la tolérance). Si un code d'élément référencé n'existe pas
encore en base, la règle est quand même créée — elle restera simplement sans
effet tant que les données correspondantes ne seront pas importées, et le moteur
de cohérence saute les cellules incomplètes.

Usage :  python manage.py seed_rules
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from collecte.models import DataElement
from qualite.models import ValidationRule


# (code, nom, gauche, opérateur, droite[liste], facteur, tolérance%, gravité, message)
RULES = [
    # --- Niveau 1 : A op B ----------------------------------------------
    ("R_TDR_POS_FAIT", "TDR positifs ≤ TDR réalisés",
     "TDR_POS", "LE", ["TDR_FAIT"], 1, 0, "BLOCK",
     "Plus de TDR positifs que de TDR réalisés."),
    ("R_TESTE_SUSP", "Cas testés ≤ cas suspects",
     "SUS_TESTE", "LE", ["SUS"], 1, 0, "BLOCK",
     "Plus de cas testés que de cas suspects."),
    ("R_CONF_SUSP", "Cas confirmés ≤ cas suspects",
     "CONF_TOT", "LE", ["SUS"], 1, 0, "WARN",
     "Plus de cas confirmés que de cas suspects."),
    ("R_DECES_CONF", "Décès ≤ cas confirmés",
     "DECES", "LE", ["CONF_TOT"], 1, 0, "WARN",
     "Plus de décès que de cas confirmés."),
    ("R_HOSP_CONF", "Hospitalisés ≤ cas confirmés",
     "HOSP", "LE", ["CONF_TOT"], 1, 0, "WARN",
     "Plus de cas hospitalisés que de cas confirmés."),

    # --- Niveau 2 : A = B + C -------------------------------------------
    ("R_CONF_SOMME", "Confirmés = simples + graves",
     "CONF_TOT", "EQ", ["CONF_SIMPLE", "CONF_GRAVE"], 1, 0, "WARN",
     "Le total des cas confirmés ne correspond pas à simples + graves."),

    # --- Niveau 3 : A = B à ± tolérance ---------------------------------
    ("R_TRAITE_CONF", "Traités ≈ confirmés (± 5 %)",
     "TRAITE_CTA", "EQ", ["CONF_TOT"], 1, 5, "WARN",
     "Écart entre cas traités et cas confirmés au-delà de 5 %."),
]


class Command(BaseCommand):
    help = "Crée les règles de cohérence de départ (idempotent)."

    def handle(self, *args, **opts):
        existing_codes = set(DataElement.objects.values_list("code", flat=True))
        created, updated = 0, 0

        for code, name, left, op, right, factor, tol, sev, msg in RULES:
            needed = [left] + right
            missing = [c for c in needed if c not in existing_codes]
            if missing:
                self.stdout.write(self.style.WARNING(
                    f"⚠ {name} : élément(s) absent(s) {missing} — règle créée "
                    f"mais inactive tant que ces données ne sont pas importées."
                ))

            _, is_created = ValidationRule.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "left_code": left,
                    "operator": op,
                    "right_codes": ",".join(right),
                    "right_factor": Decimal(factor),
                    "tolerance_pct": Decimal(tol),
                    "severity": sev,
                    "message": msg,
                    "is_active": True,
                },
            )
            if is_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"✓ créée : {name}  (niveau {_level(right, tol)})"))
            else:
                updated += 1
                self.stdout.write(f"• mise à jour : {name}")

        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {created} créée(s), {updated} mise(s) à jour. "
            f"Ajuste-les dans l'admin → « Règles de cohérence »."
        ))


def _level(right, tol):
    if tol and tol > 0:
        return 3
    return 2 if len(right) > 1 else 1
