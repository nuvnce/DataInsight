"""
seed_dimensions.py — Commande Django : crée les dimensions de désagrégation
============================================================================

À placer dans :  metadata/management/commands/seed_dimensions.py
À lancer avec :  python manage.py seed_dimensions

RÔLE
----
Créer les trois dimensions de désagrégation révélées par le fichier DHIS2 :
  - Milieu   (Communautaire / Formation sanitaire)   ← simple pour l'instant
  - Secteur  (Public / Privé)
  - Tranche  (Moins de 5 ans / 5 ans et plus sans FE / Femme enceinte)

Puis créer un COMBO « Milieu × Secteur × Tranche » et générer AUTOMATIQUEMENT
toutes ses combinaisons concrètes (produit cartésien), car c'est vers ces
combinaisons que l'import routera les valeurs.

Idempotent : réexécutable sans créer de doublon.

POUR AJOUTER UN MILIEU PLUS TARD
--------------------------------
Ajoute simplement une entrée dans MILIEUX (ci-dessous) et relance la commande.
Les nouvelles combinaisons seront générées ; les anciennes restent intactes.
"""
from itertools import product

from django.core.management.base import BaseCommand
from django.db import transaction

from metadata.models import Category, CategoryOption, CategoryCombo, CategoryOptionCombo


# ---------------------------------------------------------------------------
#  DÉFINITION DES DIMENSIONS  (c'est ICI que tu ajustes)
#  Format : (code_catégorie, nom_catégorie, [(code_option, nom_option), ...])
# ---------------------------------------------------------------------------
MILIEUX = ("MILIEU", "Milieu", [
    ("COMM", "Communautaire (ASC)"),
    ("FS",   "Formation sanitaire (USP)"),
    ("HOSP", "Hôpital (HD/HH)"),
    ("AGG",  "Agrégé district (toutes sources)"),
    # Pour affiner plus tard, décommente / ajoute, par ex. :
    # ("HR", "Hôpital de référence"),
])

SECTEURS = ("SECTEUR", "Secteur", [
    ("PUB", "Public"),
    ("PRV", "Privé"),
])

TRANCHES = ("AGE", "Tranche d'âge", [
    ("TOT", "Total (tous âges)"),
    ("U5",  "Moins de 5 ans"),
    ("O5",  "5 ans et plus sans FE"),
    ("FE",  "Femme enceinte"),
])

# Les dimensions à combiner dans le grand combo.
DIMENSIONS = [MILIEUX, SECTEURS, TRANCHES]

COMBO_CODE = "MILIEU_SECTEUR_AGE"
COMBO_NAME = "Milieu × Secteur × Âge"


class Command(BaseCommand):
    help = "Crée les dimensions de désagrégation (Milieu, Secteur, Âge) et leurs combinaisons."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Création des dimensions de désagrégation…")

        # 1) Créer chaque catégorie et ses options
        categories = {}          # code_cat -> Category
        options_by_cat = {}      # code_cat -> [CategoryOption, ...] (ordre stable)
        for cat_code, cat_name, opts in DIMENSIONS:
            cat, _ = Category.objects.get_or_create(
                code=cat_code, defaults={"name": cat_name}
            )
            categories[cat_code] = cat
            opt_objs = []
            for opt_code, opt_name in opts:
                o, _ = CategoryOption.objects.get_or_create(
                    category=cat, code=opt_code, defaults={"name": opt_name}
                )
                opt_objs.append(o)
            options_by_cat[cat_code] = opt_objs
            self.stdout.write(self.style.SUCCESS(
                f"  Catégorie « {cat_name} » : {len(opt_objs)} options."
            ))

        # 2) Créer le combo et y rattacher les catégories
        combo, _ = CategoryCombo.objects.get_or_create(
            code=COMBO_CODE, defaults={"name": COMBO_NAME}
        )
        combo.categories.set([categories[c[0]] for c in DIMENSIONS])

        # 3) Générer le produit cartésien des options -> combinaisons concrètes
        #    product([m1,m2], [p1,p2], [a1,a2,a3]) donne tous les triplets.
        option_lists = [options_by_cat[c[0]] for c in DIMENSIONS]
        n_created = 0
        for combo_tuple in product(*option_lists):
            # Nom lisible : « Communautaire (ASC), Public, Moins de 5 ans »
            name = ", ".join(o.name for o in combo_tuple)
            coc, created = CategoryOptionCombo.objects.get_or_create(
                combo=combo, name=name
            )
            # (Re)fixe les options de cette combinaison
            coc.options.set(list(combo_tuple))
            if created:
                n_created += 1

        total = CategoryOptionCombo.objects.filter(combo=combo).count()
        self.stdout.write(self.style.SUCCESS(
            f"  Combo « {COMBO_NAME} » : {total} combinaisons "
            f"({n_created} créées cette fois)."
        ))
        self.stdout.write(self.style.SUCCESS("Dimensions prêtes."))
