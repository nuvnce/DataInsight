"""Tests du tableau de bord (Tranche 4a)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from metadata.models import OrgUnit, CategoryCombo, CategoryOptionCombo, DataElement
from collecte.models import DataValue


class DashboardTests(TestCase):
    def setUp(self):
        self.pays = OrgUnit.objects.create(name="Togo", code="TG", level=1)
        self.region = OrgUnit.objects.create(name="Maritime", code="TG-M", parent=self.pays)
        self.fs1 = OrgUnit.objects.create(name="CHU", code="CHU", parent=self.region)
        self.fs2 = OrgUnit.objects.create(name="Agoè", code="AGOE", parent=self.region)
        self.combo = CategoryCombo.objects.create(name="(défaut)", code="default", is_default=True)
        self.coc = CategoryOptionCombo.objects.create(combo=self.combo, name="(défaut)")
        self.de = DataElement.objects.create(name="Cas confirmés", code="CONF", category_combo=self.combo)
        for period, (v1, v2) in {"202607": (100, 80), "202608": (120, 90)}.items():
            DataValue.objects.create(data_element=self.de, org_unit=self.fs1, period=period,
                                     category_option_combo=self.coc, value=Decimal(v1))
            DataValue.objects.create(data_element=self.de, org_unit=self.fs2, period=period,
                                     category_option_combo=self.coc, value=Decimal(v2))
        self.user = User.objects.create_superuser("adm5", "a@b.c", "pwd12345")
        self.client = Client(); self.client.force_login(self.user)

    def test_dashboard_loads(self):
        resp = self.client.get(reverse("dashboard:dashboard"), {"element": "CONF"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["has_data"])

    def test_grand_total_is_sum(self):
        resp = self.client.get(reverse("dashboard:dashboard"), {"element": "CONF"})
        # 100+80+120+90 = 390
        self.assertEqual(resp.context["grand_total"], 390.0)

    def test_filter_by_subtree(self):
        # En filtrant sur une unité feuille, on ne voit que ses valeurs
        resp = self.client.get(reverse("dashboard:dashboard"),
                               {"element": "CONF", "org_unit": self.fs1.id})
        self.assertEqual(resp.context["grand_total"], 220.0)  # 100+120

    def test_periods_sorted(self):
        resp = self.client.get(reverse("dashboard:dashboard"), {"element": "CONF"})
        self.assertEqual(resp.context["periods"], ["202607", "202608"])

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse("dashboard:dashboard"))
        self.assertEqual(resp.status_code, 302)
