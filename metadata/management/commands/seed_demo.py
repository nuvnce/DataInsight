"""
Jeu de données de démonstration (Tranche 1).

Crée une hiérarchie d'unités d'organisation du Togo, les catégories de
désagrégation usuelles (sexe, tranche d'âge) et un ensemble d'éléments de
données de lutte contre le paludisme. Idempotent : réexécutable sans doublon.

Usage :  python manage.py seed_demo
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from metadata.models import (
    OrgUnit, Category, CategoryOption, CategoryCombo,
    CategoryOptionCombo, DataElement,
)


class Command(BaseCommand):
    help = "Charge un jeu de données de démonstration (Togo / paludisme)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Chargement du jeu de démonstration…")

        # --- Unités d'organisation : Togo > Régions > District > FS ---
        pays, _ = OrgUnit.objects.get_or_create(
            code="TG", defaults={"name": "Togo", "level": 1}
        )
        regions_def = [
            ("TG-M", "Région Maritime"),
            ("TG-P", "Région des Plateaux"),
            ("TG-C", "Région Centrale"),
            ("TG-K", "Région de la Kara"),
            ("TG-S", "Région des Savanes"),
        ]
        regions = {}
        for code, name in regions_def:
            ou, _ = OrgUnit.objects.get_or_create(
                code=code, defaults={"name": name, "parent": pays}
            )
            regions[code] = ou

        # Un district sous la région Maritime, avec quelques formations sanitaires
        district, _ = OrgUnit.objects.get_or_create(
            code="TG-M-GOL", defaults={"name": "District du Golfe", "parent": regions["TG-M"]}
        )
        fs_def = [
            ("TG-M-GOL-CHU", "CHU Sylvanus Olympio"),
            ("TG-M-GOL-BE", "CMS de Bè"),
            ("TG-M-GOL-AGOE", "USP d'Agoè"),
        ]
        for code, name in fs_def:
            OrgUnit.objects.get_or_create(
                code=code, defaults={"name": name, "parent": district}
            )

        n_ou = OrgUnit.objects.count()
        self.stdout.write(self.style.SUCCESS(f"  {n_ou} unités d'organisation."))

        # --- Combo par défaut (éléments non désagrégés) ---
        default_combo, _ = CategoryCombo.objects.get_or_create(
            code="default", defaults={"name": "(par défaut)", "is_default": True}
        )
        CategoryOptionCombo.objects.get_or_create(
            combo=default_combo, name="(défaut)"
        )

        # --- Catégories de désagrégation : Sexe, Tranche d'âge ---
        sexe, _ = Category.objects.get_or_create(code="SEXE", defaults={"name": "Sexe"})
        opt_m, _ = CategoryOption.objects.get_or_create(category=sexe, code="M", defaults={"name": "Masculin"})
        opt_f, _ = CategoryOption.objects.get_or_create(category=sexe, code="F", defaults={"name": "Féminin"})

        age, _ = Category.objects.get_or_create(code="AGE", defaults={"name": "Tranche d'âge"})
        opt_u5, _ = CategoryOption.objects.get_or_create(category=age, code="U5", defaults={"name": "Moins de 5 ans"})
        opt_o5, _ = CategoryOption.objects.get_or_create(category=age, code="O5", defaults={"name": "5 ans et plus"})

        # Combo « Sexe x Âge » avec ses 4 combinaisons concrètes
        sa_combo, _ = CategoryCombo.objects.get_or_create(
            code="SEXE_AGE", defaults={"name": "Sexe × Âge"}
        )
        sa_combo.categories.set([sexe, age])
        for os_, oa in [(opt_m, opt_u5), (opt_m, opt_o5), (opt_f, opt_u5), (opt_f, opt_o5)]:
            coc, _ = CategoryOptionCombo.objects.get_or_create(
                combo=sa_combo, name=f"{os_.name}, {oa.name}"
            )
            coc.options.set([os_, oa])

        self.stdout.write(self.style.SUCCESS(
            f"  {Category.objects.count()} catégories, "
            f"{CategoryOptionCombo.objects.count()} combinaisons de désagrégation."
        ))

        # --- Éléments de données paludisme ---
        PI = DataElement.ValueType.POSITIVE_INTEGER
        SUM = DataElement.AggregationType.SUM
        elements = [
            ("PAL_CAS_SUSP", "Cas suspects de paludisme", default_combo),
            ("PAL_TDR_REAL", "TDR réalisés", default_combo),
            ("PAL_TDR_POS",  "TDR positifs", default_combo),
            ("PAL_CAS_CONF", "Cas confirmés de paludisme", sa_combo),
            ("PAL_CAS_TRAIT", "Cas traités", default_combo),
            ("PAL_CAS_HOSP", "Cas hospitalisés", default_combo),
            ("PAL_CAS_REF",  "Cas référés", default_combo),
            ("PAL_DECES",    "Décès dus au paludisme", default_combo),
            ("PAL_MILDA_DIST", "MILDA distribuées", default_combo),
        ]
        for code, name, combo in elements:
            DataElement.objects.get_or_create(
                code=code,
                defaults={"name": name, "value_type": PI,
                          "aggregation_type": SUM, "category_combo": combo},
            )

        self.stdout.write(self.style.SUCCESS(
            f"  {DataElement.objects.count()} éléments de données."
        ))

        # --- Formulaires de collecte (data sets) ---
        from collecte.models import DataSet
        from collecte import periods as P

        # Formulaire mensuel : surveillance paludisme (la plupart des éléments)
        mensuel, _ = DataSet.objects.get_or_create(
            code="PAL_MENSUEL",
            defaults={"name": "Rapport mensuel paludisme", "period_type": P.MONTHLY},
        )
        mensuel.data_elements.set(DataElement.objects.exclude(code="PAL_MILDA_DIST"))
        # Unités : les formations sanitaires (feuilles) du district du Golfe
        fs_qs = OrgUnit.objects.filter(code__startswith="TG-M-GOL-")
        mensuel.org_units.set(fs_qs)

        # Formulaire trimestriel : distribution MILDA
        trimestriel, _ = DataSet.objects.get_or_create(
            code="MILDA_TRIM",
            defaults={"name": "Distribution MILDA (trimestriel)", "period_type": P.QUARTERLY},
        )
        trimestriel.data_elements.set(DataElement.objects.filter(code="PAL_MILDA_DIST"))
        trimestriel.org_units.set(fs_qs)

        self.stdout.write(self.style.SUCCESS(
            f"  {DataSet.objects.count()} formulaires de collecte."
        ))

        # --- Valeurs de démonstration : historique mensuel réaliste ---
        # Quelques mois de données pour les 3 formations sanitaires, afin que le
        # tableau de bord ait de quoi afficher des tendances. Valeurs INVENTÉES.
        from collecte.models import DataValue
        import random
        random.seed(42)  # reproductible

        fs_list = list(OrgUnit.objects.filter(code__startswith="TG-M-GOL-"))
        months = ["202603", "202604", "202605", "202606", "202607", "202608"]
        # profils de base par élément (valeur moyenne mensuelle indicative)
        base = {
            "PAL_CAS_SUSP": 500, "PAL_TDR_REAL": 460, "PAL_TDR_POS": 300,
            "PAL_CAS_TRAIT": 290, "PAL_CAS_HOSP": 40, "PAL_CAS_REF": 12,
            "PAL_DECES": 2,
        }
        default_coc = CategoryOptionCombo.objects.get(combo=default_combo)
        n_val = 0
        for fs in fs_list:
            factor = random.uniform(0.7, 1.4)  # taille relative de la FS
            for code, mean in base.items():
                de = DataElement.objects.get(code=code)
                for m in months:
                    # variation saisonnière + bruit
                    month_num = int(m[4:6])
                    season = 1.0 + 0.35 * (1 if month_num in (6, 7, 8) else 0)
                    val = max(0, round(mean * factor * season * random.uniform(0.8, 1.2)))
                    DataValue.objects.update_or_create(
                        data_element=de, org_unit=fs, period=m,
                        category_option_combo=default_coc,
                        defaults={"value": val},
                    )
                    n_val += 1

        self.stdout.write(self.style.SUCCESS(f"  {n_val} valeurs de démonstration (6 mois)."))
        self.stdout.write(self.style.SUCCESS("Jeu de démonstration chargé."))
