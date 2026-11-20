"""Service control: status, actions, laptop commands over the signed check-in, central settings."""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from governance.models import InstallCommand, InstallRegistration, ServiceRun
from governance.services import policy_sync, service_control as sc


def _fake_actions(**fns):
    table = {k: dict(v) for k, v in sc.ACTIONS.items()}
    for key, fn in fns.items():
        table[key]["fn"] = fn
    return mock.patch.dict(sc.ACTIONS, table)


class ActionTests(TestCase):
    def test_run_action_logs_success_and_failure(self):
        def boom(args):
            raise RuntimeError("ITMS unreachable")
        with _fake_actions(**{"order_sync.run_now": lambda a: "12 orders", "kit_sync.run_now": boom}):
            ok = sc.run_action("order_sync.run_now", triggered_by="owner", wait=True)
            bad = sc.run_action("kit_sync.run_now", triggered_by="owner", wait=True)
        ok.refresh_from_db(); bad.refresh_from_db()
        self.assertTrue(ok.ok); self.assertEqual(ok.summary, "12 orders")
        self.assertFalse(bad.ok); self.assertIn("ITMS unreachable", bad.summary)

    def test_unknown_action_refused(self):
        with self.assertRaises(ValueError):
            sc.run_action("os.system", wait=True)

    def test_failing_detects_errors(self):
        self.assertEqual(sc.failing({"order_sync": {"last_result": "ERROR: 502"}, "kit_sync": {"last_result": "ok"}}),
                         ["ITMS order sync"])

    def test_status_all_never_raises(self):
        status = sc.status_all()
        for key in sc.SERVICE_LABELS:
            self.assertIn(key, status)


@override_settings(DEPLOYMENT_ROLE="central")
class CommandFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        self.inst = InstallRegistration.objects.create(name="AGM laptop", install_id="inst-1", secret="s3cret",
                                                       status="ACTIVE")

    def _beat(self, payload):
        body = json.dumps(payload).encode()
        ts, sig = policy_sync.sign("s3cret", body)
        return policy_sync.handle_heartbeat("inst-1", body, ts, sig)

    def test_queued_command_is_delivered_once_and_result_recorded(self):
        cmd = policy_sync.queue_command(self.inst, "outbox.drain", self.admin)
        resp = self._beat({"services": {"outbox": {"queued": 3}}})
        self.assertEqual([c["command"] for c in resp["commands"]], ["outbox.drain"])
        cmd.refresh_from_db(); self.assertEqual(cmd.status, "SENT")
        self.inst.refresh_from_db(); self.assertEqual(self.inst.services["outbox"]["queued"], 3)
        self._beat({"command_results": [{"id": cmd.pk, "ok": True, "summary": "3 sent"}]})
        cmd.refresh_from_db()
        self.assertEqual((cmd.status, cmd.result), ("DONE", "3 sent"))
        self.assertEqual(self._beat({})["commands"], [])

    def test_only_whitelisted_commands_can_be_queued(self):
        with self.assertRaises(policy_sync.PolicySyncError):
            policy_sync.queue_command(self.inst, "rm -rf", self.admin)

    def test_edge_runs_whitelisted_commands_once(self):
        calls = []
        with _fake_actions(**{"vision.reload": lambda a: calls.append(1) or "reloaded"}), \
                mock.patch("governance.services.service_control.threading.Thread") as thread:
            thread.side_effect = lambda target, **kw: mock.Mock(start=target)
            policy_sync._run_commands([{"id": 7, "command": "vision.reload"}, {"id": 8, "command": "shell"}])
            policy_sync._run_commands([{"id": 7, "command": "vision.reload"}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(policy_sync._local_command_results(), [{"id": "7", "ok": True, "summary": "reloaded"}])
        self.assertFalse(ServiceRun.objects.filter(action="shell").exists())


class CentralSettingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")

    def test_validation(self):
        with self.assertRaises(ValueError):
            sc.parse_setting("vision.detector_conf_threshold", "5")
        with self.assertRaises(ValueError):
            sc.parse_setting("vision.yolo_weights", "../../etc/passwd")
        self.assertEqual(sc.parse_setting("sync.order_sync_interval_seconds", "600"), 600)
        self.assertTrue(sc.parse_setting("sync.auto_sync_enabled", "on"))

    def test_central_setting_reaches_installs(self):
        with mock.patch.object(sc, "apply_setting_locally") as applied:
            sc.set_central_setting("vision.detector_conf_threshold", "0.4", self.admin)
        applied.assert_called_once_with("vision.detector_conf_threshold", 0.4)
        flags = {f["key"]: f["value"] for f in policy_sync.build_snapshot()["flags"]}
        self.assertEqual(flags["setting.vision.detector_conf_threshold"], 0.4)
        with mock.patch.object(sc, "apply_setting_locally") as applied, \
                mock.patch("core.services.config_service.get_setting", return_value=0.25):
            policy_sync._apply_central_setting("vision.detector_conf_threshold", 0.4)
            policy_sync._apply_central_setting("not.a.setting", 1)
        applied.assert_called_once_with("vision.detector_conf_threshold", 0.4)


@override_settings(DEPLOYMENT_ROLE="central", GOVERNANCE_ENFORCE=True)
class ServicesPageTests(TestCase):
    def setUp(self):
        cache.clear()
        from backoffice.tests.test_backoffice import admin_client_with_2fa
        self.admin = User.objects.create_superuser("owner", "o@example.com", "Owner-pass-1")
        self.c = admin_client_with_2fa(self.admin)
        self.inst = InstallRegistration.objects.create(name="AGM laptop", install_id="inst-1", secret="x",
                                                       status="ACTIVE")

    def test_page_renders_and_send_needs_password(self):
        self.assertContains(self.c.get("/backoffice/services/"), "AGM laptop")
        data = {"kind": "action", "target": str(self.inst.pk), "action": "sync.pause"}
        self.c.post("/backoffice/services/", data)
        self.assertFalse(InstallCommand.objects.exists())
        self.c.post("/backoffice/services/", {**data, "confirm_password": "Owner-pass-1"})
        self.assertEqual(InstallCommand.objects.get().command, "sync.pause")

    def test_unknown_action_rejected(self):
        self.c.post("/backoffice/services/", {"kind": "action", "target": "all", "action": "evil",
                                              "confirm_password": "Owner-pass-1"})
        self.assertFalse(InstallCommand.objects.exists())


class SubmissionBackendSafetyTests(TestCase):
    def test_stray_mock_config_ignored_in_production(self):
        from core.services import submission_worker
        pair = mock.Mock(order=None, operator=None, id=1)
        with mock.patch("core.services.config_service.get_setting",
                        side_effect=lambda k, d=None, *a: "mock" if k == "submission.backend" else d), \
                override_settings(DEBUG=False, IS_TESTING=False, ITMS_SUBMISSION_BACKEND="web"), \
                mock.patch.object(submission_worker, "_log"):
            outcome = submission_worker.submit_pair(pair)
        self.assertEqual(outcome.backend, "web")

    def test_services_page_shows_sync_account(self):
        from core.models import OperatorITMSSession
        user = User.objects.create_user("tl_sync", "tl@example.com", "x")
        OperatorITMSSession.objects.create(user=user, itms_email="tl@itms-ug.com", is_authenticated=True)
        self.assertEqual(sc.status_all()["order_sync"]["account"], "tl@itms-ug.com")
