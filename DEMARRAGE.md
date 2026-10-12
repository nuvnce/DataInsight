# DataInsight — monter la première version

Projet Django complet : toutes les briques d'analyse (imports, population,
cartes, tableaux de bord, indicateurs) plus le socle de la couche 1 de
fiabilisation (app `qualite`).

## 1. Dézipper et entrer dans le dossier

```powershell
cd "D:\DANIEL ESSONANI"
# après avoir dézippé DataInsight.zip ici
cd DataInsight
```

## 2. Environnement et dépendances

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> `geopandas` n'est utile que pour charger les frontières des districts
> (commande `load_boundaries`). S'il refuse de s'installer, retire sa ligne de
> `requirements.txt` et relance `pip install -r requirements.txt` : tout le
> reste marchera, et on chargera les frontières par la voie de secours (GeoJSON).

## 3. Base, dimensions, compte admin

```powershell
python manage.py migrate
python manage.py seed_dimensions
python manage.py createsuperuser
```

## 4. Lancer

```powershell
python manage.py runserver
```

Ouvre `http://127.0.0.1:8000/` → le tableau de bord. L'admin est sur `/admin/`,
où tu trouves maintenant « Lots d'import », « Règles de cohérence », etc.
(la couche 1).

## 5. Charger les données de référence (quand tu les as)

Dans `python manage.py shell` :
```python
from collecte.orgunit_import import import_org_units
import_org_units(r"chemin\vers\extraction_DHIS2.xlsx")   # la hiérarchie

from collecte.population_service import import_population
import_population(r"chemin\vers\population_RGPH.xlsx")    # la population
```
Puis, en ligne de commande :
```powershell
python manage.py load_boundaries "chemin\vers\Préfecture.shp"   # les frontières
python manage.py seed_indicators                                # les indicateurs
```

## 6. Mettre sur GitHub

Le `.gitignore` est déjà en place (il protège la base, le venv, les données).
```powershell
git init
git add .
git commit -m "Initial : DataInsight — plateforme PNLP paludisme"
git branch -M main
git remote add origin https://github.com/nuvnce/datainsight.git
git push -u origin main
```

> Avant le push, un `git status` : tu ne dois voir ni `db.sqlite3`, ni `.venv/`,
> ni de fichiers `.xlsx`. Si c'est le cas, c'est bon.

## État des lieux

**Fonctionnel** : imports (DHIS2, population), hiérarchie org units,
cartographie, tableaux de bord thématiques, indicateurs.

**Couche 1 (en construction)** : le socle de données est posé (lots, versions,
règles, contrôles, anomalies). Les moteurs — complétude, cohérence, atypiques —
et leurs écrans viennent dans les prochaines briques.
