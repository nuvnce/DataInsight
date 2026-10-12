"""
load_boundaries — charge les limites géographiques des districts (préfectures)
================================================================================

À placer dans : metadata/management/commands/load_boundaries.py

Lit un shapefile de préfectures (39 = les districts DHIS2), simplifie les
polygones pour le web, et remplit le champ `geometry` (GeoJSON texte) de chaque
OrgUnit de niveau 3, par correspondance de nom normalisé.

Reste 100 % chemin A : GeoJSON dans un TextField, pas de PostGIS.

Usage :
    python manage.py load_boundaries "chemin/vers/Préfecture.shp"
    python manage.py load_boundaries "...Préfecture.shp" --name-field PREFECTURE --tolerance 0.001
    python manage.py load_boundaries "...Préfecture.shp" --dry-run

Dépendance : geopandas (pip install geopandas). Utilisé uniquement par cette
commande d'import ponctuel, pas au runtime de l'application.
"""
import json
import re
import unicodedata

from django.core.management.base import BaseCommand, CommandError

from metadata.models import OrgUnit


def _norm(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip().replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class Command(BaseCommand):
    help = "Charge les limites géographiques des districts depuis un shapefile."

    def add_arguments(self, parser):
        parser.add_argument("shapefile", help="Chemin du .shp des préfectures/districts.")
        parser.add_argument("--name-field", default="PREFECTURE",
                            help="Colonne du nom de district (défaut : PREFECTURE).")
        parser.add_argument("--level", type=int, default=3,
                            help="Niveau OrgUnit à faire correspondre (défaut : 3 = district).")
        parser.add_argument("--tolerance", type=float, default=0.001,
                            help="Tolérance de simplification des polygones (défaut : 0.001).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Vérifie les correspondances sans rien enregistrer.")

    def handle(self, *args, **opts):
        try:
            import geopandas as gpd
        except ImportError:
            raise CommandError(
                "geopandas n'est pas installé. Lancez : pip install geopandas"
            )
        import warnings
        warnings.filterwarnings("ignore")

        path = opts["shapefile"]
        name_field = opts["name_field"]
        level = opts["level"]
        tol = opts["tolerance"]
        dry = opts["dry_run"]

        try:
            gdf = gpd.read_file(path)
        except Exception as e:
            raise CommandError(f"Impossible de lire le shapefile : {e}")

        if name_field not in gdf.columns:
            raise CommandError(
                f"Colonne « {name_field} » absente. Colonnes disponibles : "
                f"{list(gdf.columns)}"
            )

        # CRS : forcer WGS84 (lat/lon) si non déclaré ou différent, pour Leaflet
        if gdf.crs is None:
            gdf = gdf.set_crs(4326, allow_override=True)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)

        # Simplifier les polygones (allège fortement le GeoJSON pour le web)
        gdf["geometry"] = gdf.geometry.simplify(tol, preserve_topology=True)

        # Index des OrgUnit du niveau visé, par nom normalisé
        ou_index = {_norm(ou.name): ou for ou in OrgUnit.objects.filter(level=level)}
        if not ou_index:
            raise CommandError(
                f"Aucune unité d'organisation de niveau {level} en base. "
                f"Chargez d'abord la hiérarchie DHIS2 (import_org_units)."
            )

        matched, unmatched, saved = [], [], 0
        for _, row in gdf.iterrows():
            raw_name = row[name_field]
            key = _norm(raw_name)
            ou = ou_index.get(key)
            if ou is None:
                unmatched.append(str(raw_name))
                continue
            matched.append(str(raw_name))
            if not dry:
                geojson = json.dumps(row.geometry.__geo_interface__)
                ou.geometry = geojson
                ou.save(update_fields=["geometry"])
                saved += 1

        self.stdout.write(self.style.SUCCESS(
            f"Correspondances : {len(matched)} / {len(gdf)} entités du shapefile."
        ))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"Non reconnues ({len(unmatched)}) : {', '.join(sorted(unmatched))}"
            ))
        if dry:
            self.stdout.write("Simulation : rien n'a été enregistré.")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{saved} limites enregistrées dans OrgUnit.geometry (niveau {level})."
            ))
