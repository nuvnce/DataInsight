"""Tests de la tranche 1 (socle métadonnées)."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (
    OrgUnit, Category, CategoryOption, CategoryCombo,
    CategoryOptionCombo, DataElement,
)


class OrgUnitHierarchyTests(TestCase):
    def setUp(self):
        self.pays = OrgUnit.objects.create(name="Togo", code="TG", level=1)
        self.region = OrgUnit.objects.create(name="Maritime", code="TG-M", parent=self.pays)
        self.district = OrgUnit.objects.create(name="Golfe", code="TG-M-GOL", parent=self.region)

    def test_level_auto_computed_from_parent(self):
        self.assertEqual(self.region.level, 2)
        self.assertEqual(self.district.level, 3)

    def test_path_materialized(self):
        self.assertEqual(self.pays.path, f"/{self.pays.pk}/")
        self.assertEqual(
            self.district.path,
            f"/{self.pays.pk}/{self.region.pk}/{self.district.pk}/",
        )

    def test_ancestors(self):
        anc = self.district.ancestors()
        self.assertEqual([a.code for a in anc], ["TG", "TG-M"])

    def test_is_leaf(self):
        self.assertFalse(self.pays.is_leaf)
        self.assertTrue(self.district.is_leaf)

    def test_root_must_be_level_1(self):
        ou = OrgUnit(name="Bad", code="BAD", level=3)
        with self.assertRaises(ValidationError):
            ou.full_clean()

    def test_subtree_query_via_path(self):
        # Toutes les unités sous le pays (préfixe de path)
        descendants = OrgUnit.objects.filter(path__startswith=self.pays.path)
        self.assertEqual(descendants.count(), 3)  # pays inclus


class DisaggregationTests(TestCase):
    def test_option_combo_holds_options(self):
        sexe = Category.objects.create(name="Sexe", code="SEXE")
        m = CategoryOption.objects.create(category=sexe, name="Masculin", code="M")
        age = Category.objects.create(name="Âge", code="AGE")
        u5 = CategoryOption.objects.create(category=age, name="<5", code="U5")
        combo = CategoryCombo.objects.create(name="Sexe × Âge", code="SA")
        combo.categories.set([sexe, age])
        coc = CategoryOptionCombo.objects.create(combo=combo, name="Masculin, <5")
        coc.options.set([m, u5])
        self.assertEqual(coc.options.count(), 2)

    def test_option_code_unique_per_category(self):
        from django.db import IntegrityError
        sexe = Category.objects.create(name="Sexe", code="SEXE")
        CategoryOption.objects.create(category=sexe, name="Masculin", code="M")
        with self.assertRaises(IntegrityError):
            CategoryOption.objects.create(category=sexe, name="Dup", code="M")


class DataElementTests(TestCase):
    def test_create_element_with_default_combo(self):
        combo = CategoryCombo.objects.create(name="(défaut)", code="default", is_default=True)
        de = DataElement.objects.create(
            name="Cas confirmés", code="PAL_CAS_CONF", category_combo=combo,
        )
        self.assertEqual(de.value_type, DataElement.ValueType.POSITIVE_INTEGER)
        self.assertEqual(de.aggregation_type, DataElement.AggregationType.SUM)
