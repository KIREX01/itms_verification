import os
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from core.services.secure_storage import (
    get_secure_dir,
    get_secure_auth_dir,
    get_secure_config_path,
    get_secure_auth_path,
    set_secure_file_permissions,
)
from core.services import config_service, auth_service, itms_web_client


class SecureStorageAndFileHandlingTests(TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_secure_directories_creation(self):
        secure_dir = get_secure_dir(self.temp_dir)
        self.assertTrue(secure_dir.is_dir())
        self.assertEqual(secure_dir.name, 'secure')

        auth_dir = get_secure_auth_dir(self.temp_dir)
        self.assertTrue(auth_dir.is_dir())
        self.assertEqual(auth_dir.name, 'auth')
        self.assertEqual(auth_dir.parent, secure_dir)

    def test_secure_config_path_migration_from_root(self):
        # Simulate legacy config.json in root
        legacy_cfg = self.temp_dir / 'config.json'
        sample_data = {'system': {'app_title': 'Migrated App'}, 'database': {'engine': 'sqlite'}}
        legacy_cfg.write_text(json.dumps(sample_data), encoding='utf-8')

        # Request secure config path
        resolved = get_secure_config_path(self.temp_dir)
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.parent.name, 'secure')
        self.assertEqual(resolved.name, 'config.json')

        # Verify contents migrated properly
        loaded = json.loads(resolved.read_text(encoding='utf-8'))
        self.assertEqual(loaded['system']['app_title'], 'Migrated App')

    def test_secure_auth_path_migration_from_vault(self):
        # Create a fake legacy vault file in temp_dir/media/vault/.operator_session.json
        vault_dir = self.temp_dir / 'media' / 'vault'
        vault_dir.mkdir(parents=True, exist_ok=True)
        legacy_auth = vault_dir / '.operator_session.json'
        legacy_auth.write_text(json.dumps({'username': 'operator1', 'token': 'abc123xyz'}), encoding='utf-8')

        with patch('core.services.secure_storage.settings') as mock_settings:
            mock_settings.configured = True
            mock_settings.BASE_DIR = self.temp_dir
            mock_settings.VAULT_ROOT = vault_dir

            resolved = get_secure_auth_path('operator_session.json', legacy_vault_file='.operator_session.json', base_dir=self.temp_dir)
            self.assertTrue(resolved.is_file())
            self.assertEqual(resolved.name, 'operator_session.json')
            self.assertEqual(resolved.parent.name, 'auth')

            # Legacy file in vault should have been moved/unlinked
            self.assertFalse(legacy_auth.exists())

            # Data should be intact
            data = json.loads(resolved.read_text(encoding='utf-8'))
            self.assertEqual(data['username'], 'operator1')

    def test_config_service_uses_secure_storage(self):
        cfg_path = config_service.get_config_path()
        self.assertIn('secure', str(cfg_path))
        self.assertTrue(cfg_path.name.endswith('config.json'))

    def test_auth_service_uses_secure_storage(self):
        sess_path = auth_service.get_session_file_path()
        prefs_path = auth_service.get_prefs_file_path()
        self.assertIn('secure', str(sess_path))
        self.assertIn('auth', str(sess_path))
        self.assertIn('secure', str(prefs_path))
        self.assertIn('auth', str(prefs_path))

    def test_itms_web_client_uses_secure_storage(self):
        web_sess = itms_web_client.get_default_session_file()
        self.assertIn('secure', str(web_sess))
        self.assertIn('auth', str(web_sess))
        self.assertEqual(web_sess.name, 'itms_web_session.json')

