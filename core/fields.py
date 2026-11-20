"""Model fields."""
import base64
import hashlib
import json

from django.conf import settings
from django.db import models

_PREFIX = "fernet:"


def _fernet():
    from cryptography.fernet import Fernet
    key = hashlib.sha256(("close-field-seal:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class SealedJSONField(models.JSONField):
    """
    JSON encrypted at rest (Fernet, key derived from SECRET_KEY). Reads and writes look like a normal JSONField.
    Legacy plaintext values are read as-is and encrypted on the next save. If SECRET_KEY changes, the value reads
    as empty (for ITMS cookies that simply means signing in to ITMS again).
    """

    def get_prep_value(self, value):
        if value is None:
            return None
        token = _fernet().encrypt(json.dumps(value, cls=self.encoder).encode()).decode()
        return super().get_prep_value(_PREFIX + token)

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        if isinstance(value, str) and value.startswith(_PREFIX):
            try:
                return json.loads(_fernet().decrypt(value[len(_PREFIX):].encode()).decode(), cls=self.decoder)
            except Exception:
                return self.get_default()
        return value
