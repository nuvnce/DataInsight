"""
seed_indicators — crée les indicateurs dérivés du paludisme
============================================================

Installe des indicateurs standards, calculables avec les éléments de données
importés. Idempotent : relançable sans créer de doublon.

Usage :  python manage.py seed_indicators
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from collecte.models import Indicator, DataElement


# (code, nom, numérateur, dénominateur, facteur, description)
INDICATORS = [
    ("POS_TDR", "Taux de positivité TDR", "TDR_POS", "TDR_FAIT", 100,
     "Proportion de TDR positifs parmi les TDR effectués."),
    ("TAUX_TEST", "Taux de test des cas suspects", "SUS_TESTE", "SUS", 100,
     "Proportion de cas suspects testés au TDR."),
    ("PROP_GRAVE", "Proportion de cas graves", "GRAVE_REF", "CONF_TOT", 100,
     "Part des cas confirmés qui sont graves (référés)."),
    ("TAUX_CTA", "Taux de traitement CTA", "TRAITE_CTA", "CONF_TOT", 100,
     "Part des cas confirmés traités avec CTA."),
]


class Command(BaseCommand):
    help = "Crée les indicateurs dérivés du paludisme (idempotent)."

    def handle(self, *args, **opts):
        existing_codes = set(DataElement.objects.values_list("code", flat=True))
        created, updated = 0, 0

        for code, name, num, den, factor, desc in INDICATORS:
            missing = [c for c in (num, den) if c not in existing_codes]
            if missing:
                self.stdout.write(self.style.WARNING(
                    f"⚠ {name} : élément(s) absent(s) {missing} — indicateur créé "
                    f"mais vide tant que ces données ne sont pas importées."
                ))

            _, is_created = Indicator.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "numerator_code": num,
                    "denominator_code": den,
                    "factor": Decimal(factor),
                    "description": desc,
                },
            )
            if is_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"✓ créé : {name}"))
            else:
                updated += 1
                self.stdout.write(f"• mis à jour : {name}")

        self.stdout.write(self.style.SUCCESS(
            f"\nTerminé : {created} créé(s), {updated} mis à jour."
        ))
