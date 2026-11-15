"""
Regression tests for shift-date batch scoping (16 Nov 2026 fixes):
- A batch belongs to a date by its Kampala created_at or the date segment of its ID,
  never by a loose DDMMYY substring hit in the time/random parts of the ID.
- The Batches list reports created_at in Kampala time, not UTC.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import IngestionBatch
from core.services import date_service

EAT = ZoneInfo("Africa/Kampala")


def _make_batch(batch_id: str, created_local: datetime) -> IngestionBatch:
    b = IngestionBatch.objects.create(batch_id=batch_id)
    # created_at is auto-set; overwrite it explicitly for the scenario.
    IngestionBatch.objects.filter(pk=b.pk).update(created_at=created_local)
    b.refresh_from_db()
    return b


class BatchDateScopingTests(TestCase):
    def test_ddmmyy_inside_time_part_does_not_match(self):
        # Made on 20 Oct 2026 at 13:11:26: its time part "131126" equals the
        # DDMMYY suffix of 13 Nov 2026, which used to pull it into that shift.
        _make_batch("BATCH-20261020-131126-ab12cd", datetime(2026, 10, 20, 13, 11, 26, tzinfo=EAT))
        on_13_nov = IngestionBatch.objects.filter(date_service.batches_on_date_q(date(2026, 11, 13)))
        self.assertFalse(on_13_nov.exists())

    def test_created_at_in_kampala_day_matches(self):
        # 01:30 EAT on 14 Nov is still 13 Nov in UTC; it must belong to 14 Nov.
        _make_batch("BATCH-20261113-223000-ffffff", datetime(2026, 11, 14, 1, 30, tzinfo=EAT))
        self.assertTrue(
            IngestionBatch.objects.filter(date_service.batches_on_date_q(date(2026, 11, 14))).exists()
        )

    def test_id_date_segment_matches(self):
        # Batch whose created_at was reset (e.g. restored) is still found by its ID date.
        _make_batch("BATCH-20261105-090000-aaaaaa", datetime(2026, 11, 15, 9, 0, tzinfo=EAT))
        self.assertTrue(
            IngestionBatch.objects.filter(date_service.batches_on_date_q(date(2026, 11, 5))).exists()
        )


class BatchesListLocalTimeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="op_time", password="pw12345!")
        self.client = Client()
        self.client.force_login(self.user)

    def test_created_at_reported_in_kampala_time(self):
        _make_batch("BATCH-20261114-013000-bbbbbb", datetime(2026, 11, 14, 1, 30, tzinfo=EAT))
        res = self.client.get(reverse("core:api_batches_list"), {"scope": "ALL"})
        self.assertEqual(res.status_code, 200)
        row = next(b for b in res.json()["batches"] if b["batch_id"] == "BATCH-20261114-013000-bbbbbb")
        self.assertEqual(row["created_at"], "2026-11-14 01:30:00")
        self.assertEqual(row["created_date"], "2026-11-14")
