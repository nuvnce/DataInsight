# DataInsight

Plateforme de fiabilisation et d'analyse des données de routine du paludisme,
conçue pour le Programme National de Lutte contre le Paludisme (PNLP) du Togo.

Le système s'organise en **deux couches** :

1. **Fiabilisation des données** — un fichier déposé traverse trois contrôles
   (complétude, cohérence, valeurs atypiques) qui produisent des versions
   successives signées jusqu'à une version finale validée.
2. **Analyse et restitution** — la version validée alimente l'extraction
   d'indicateurs, les cartes de stratification et les tableaux de bord.

## Pile technique

- Django 5.1 (Python 3.12)
- SQLite en développement, PostgreSQL en production
- pandas / openpyxl pour l'import Excel
- Leaflet pour la cartographie (GeoJSON)
- geopandas pour le chargement ponctuel des frontières

## Démarrage

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_dimensions  # OBLIGATOIRE avant tout import
python manage.py createsuperuser
python manage.py runserver
```

## Chargement initial des données de référence

```bash
# 1. La hiérarchie des unités d'organisation (depuis une extraction DHIS2)
python manage.py shell
>>> from collecte.orgunit_import import import_org_units
>>> import_org_units("chemin/vers/extraction_DHIS2.xlsx")

# 2. Les frontières des districts (depuis le shapefile des préfectures)
python manage.py load_boundaries "chemin/vers/Préfecture.shp"

# 3. La population de référence (fichier RGPH)
>>> from collecte.population_service import import_population
>>> import_population("chemin/vers/population_RGPH.xlsx")

# 4. Les indicateurs dérivés
python manage.py seed_indicators
```

## Structure du code

```
config/        projet Django (settings, urls)
metadata/      référentiel : unités d'organisation, désagrégations, éléments
collecte/      valeurs, imports, population, indicateurs, moteur qualité
dashboard/     tableaux de bord et cartographie
templates/     gabarit de base (barre latérale DataInsight)
```

## Important — données sensibles

Ce dépôt ne contient **que le code**. Les données réelles du PNLP (fichiers
Excel, base de données remplie, shapefiles) ne sont **jamais** versionnées —
voir `.gitignore`. La clé secrète Django et les variables d'environnement
restent également hors du dépôt.

## Statut

Projet en développement actif. La couche d'analyse (cartes, tableaux de bord,
indicateurs) est fonctionnelle. La couche de fiabilisation est en construction.
