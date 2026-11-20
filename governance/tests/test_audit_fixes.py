"""Senior-audit fixes: cookies at rest, laptop superuser scope, request-path DB writes, media caching, SSE, pipeline."""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import AuthorizedDevice, OperatorITMSSession


class SealedCookiesTests(TestCase):
    def test_cookies_encrypted_at_rest_and_readable(self):
        user = User.objects.create_user("tl", "tl@example.com", "x")
        OperatorITMSSession.objects.create(user=user, cookies={"_identity-frontend": "SECRET-SESSION"})
        with connection.cursor() as cur:
            cur.execute("SELECT cookies FROM core_operatoritmssession")
            raw = cur.fetchone()[0]
        self.assertNotIn("SECRET-SESSION", str(raw))
        self.assertIn("fernet:", str(raw))
        self.assertEqual(OperatorITMSSession.objects.get().cookies, {"_identity-frontend": "SECRET-SESSION"})

    def test_legacy_plaintext_still_reads(self):
        user = User.objects.create_user("tl", "tl@example.com", "x")
        sess = OperatorITMSSession.objects.create(user=user)
        with connection.cursor() as cur:
            cur.execute("UPDATE core_operatoritmssession SET cookies=%s WHERE id=%s", [json.dumps({"a": "b"}), sess.pk])
        self.assertEqual(OperatorITMSSession.objects.get().cookies, {"a": "b"})

    def test_wrong_key_reads_empty(self):
        user = User.objects.create_user("tl", "tl@example.com", "x")
        OperatorITMSSession.objects.create(user=user, cookies={"a": "b"})
        with self.settings(SECRET_KEY="a-different-key-" + "x" * 40):
            self.assertEqual(OperatorITMSSession.objects.get().cookies, {})


class LaptopSuperuserScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_superuser("admin", "a@example.com", "Local-admin-1")

    def _auth(self, ip):
        from governance.auth_backend import LockoutModelBackend
        req = RequestFactory().post("/login/", REMOTE_ADDR=ip)
        return LockoutModelBackend().authenticate(req, username="admin", password="Local-admin-1")

    @override_settings(DEPLOYMENT_ROLE="edge", IS_TESTING=False)
    def test_laptop_admin_only_on_this_pc(self):
        self.assertIsNotNone(self._auth("127.0.0.1"))
        self.assertIsNone(self._auth("192.168.1.44"))  # a phone / other computer on the LAN

    @override_settings(DEPLOYMENT_ROLE="central", IS_TESTING=False)
    def test_central_admin_unaffected(self):
        self.assertIsNotNone(self._auth("41.210.1.1"))


class RequestPathWriteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("tl", "tl@example.com", "x")

    def test_refresh_writes_at_most_once_a_minute(self):
        from core.services import device_access_service as das
        dev = AuthorizedDevice.objects.create(device_id="DEV-T1", user=self.user, status="APPROVED",
                                              authorized_email="tl@example.com", last_seen_at=timezone.now())
        req = RequestFactory().get("/api/stats/", REMOTE_ADDR="127.0.0.1", HTTP_USER_AGENT="UA")
        ident = {"device_id": "DEV-T1", "client_ip": "127.0.0.1", "mac_address": "", "device_name": "PC",
                 "device_type": "DESKTOP", "user_agent": dev.user_agent, "fingerprint_hash": dev.fingerprint_hash,
                 "has_valid_trust_cookie": "1"}
        dev.ip_address = "127.0.0.1"
        dev.save()
        with mock.patch.object(das, "extract_device_identity", return_value=ident), \
                mock.patch.object(AuthorizedDevice, "save", autospec=True,
                                  side_effect=AuthorizedDevice.save) as saved:
            das.register_or_refresh_authorized_device(req, user=self.user)
            self.assertEqual(saved.call_count, 0, [c.kwargs for c in saved.call_args_list])  # fresh: no write
            AuthorizedDevice.objects.filter(pk=dev.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
            das.register_or_refresh_authorized_device(req, user=self.user)
            self.assertIn(saved.call_count, (1, 2))  # refresh (+ device enrichment), once a minute

    def test_blocklist_cached_and_invalidated(self):
        from core.services import device_access_service as das
        self.assertFalse(das.is_ip_or_mac_blocked("10.0.0.1", "", "DEV-B1")[0])
        with self.assertNumQueries(0):
            das.is_ip_or_mac_blocked("10.0.0.1", "", "DEV-B1")
        AuthorizedDevice.objects.create(device_id="DEV-B1", status="BLOCKED")  # signal clears the cache
        self.assertTrue(das.is_ip_or_mac_blocked("10.0.0.1", "", "DEV-B1")[0])

    def test_new_devices_need_approval_by_default(self):
        from core.services import device_access_service as das
        self.assertTrue(das.get_security_access_config()["require_email_approval"])


class MediaAndStreamTests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "crops").mkdir()
        (self.root / "crops" / "p.jpg").write_bytes(b"\xff\xd8jpeg")
        self.user = User.objects.create_user("tl", "tl@example.com", "x")
        self.client.force_login(self.user)

    def tearDown(self):
        self.tmp.cleanup()

    def test_media_etag_and_304(self):
        with self.settings(MEDIA_ROOT=str(self.root)):
            r = self.client.get("/media/crops/p.jpg")
            self.assertEqual(r.status_code, 200)
            r.close()
            self.assertIn("private", r["Cache-Control"])
            r2 = self.client.get("/media/crops/p.jpg", HTTP_IF_NONE_MATCH=r["ETag"])
            self.assertEqual(r2.status_code, 304)

    def test_sse_ends_when_idle(self):
        with mock.patch("core.services.pipeline_runner.runner.get_status", return_value={"running": False}), \
                mock.patch("time.sleep") as slept:
            r = self.client.get("/api/stream/events/")
            body = b"".join(r.streaming_content).decode()
        self.assertIn("retry: 3000", body)
        self.assertEqual(body.count("data:"), 1)
        slept.assert_not_called()


class PipelineSubprocessTests(TestCase):
    def test_runs_in_low_priority_child_process(self):
        import io
        import sys
        from core.services import pipeline_runner as pr

        fake = mock.Mock()
        fake.stdout = io.StringIO("Phase 1: pairs\nPhase 2: vision\n")
        fake.wait.return_value = 0
        out = io.StringIO()
        with mock.patch.object(pr, "_use_subprocess", return_value=True), \
                mock.patch("subprocess.Popen", return_value=fake) as popen:
            pr.run_management_command(["process_vision", "--save-crops"], out, io.StringIO())
        args, kwargs = popen.call_args
        self.assertIn("manage.py", args[0][1])
        self.assertEqual(args[0][2:], ["process_vision", "--save-crops"])
        if sys.platform == "win32":
            self.assertTrue(kwargs["creationflags"] & 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
        else:
            self.assertIn("preexec_fn", kwargs)
        self.assertIn("Phase 2: vision", out.getvalue())

    def test_failure_raises(self):
        import io
        from core.services import pipeline_runner as pr

        fake = mock.Mock(stdout=io.StringIO("Traceback: boom\n"))
        fake.wait.return_value = 1
        with mock.patch.object(pr, "_use_subprocess", return_value=True), \
                mock.patch("subprocess.Popen", return_value=fake):
            with self.assertRaises(RuntimeError):
                pr.run_management_command(["process_vision"], io.StringIO(), io.StringIO())
