# Release Notes — ITMS Verification Copilot v1.0.8

**Release Date**: October 2026  
**Build Target**: Production Workstations & Mobile Companions (Windows, macOS, Linux, Android, iOS)  
**Release Tag**: `v1.0.8`

---

## 🎯 Executive Summary

Version 1.0.8 provides **Zero-Configuration Self-Healing SSL/HTTPS Provisioning** across the entire lifecycle of the application. It ensures that when new updates are pulled or fresh installations occur without local certificate files on disk, the system automatically and transparently generates self-signed certificates, synchronizes root legacy certificates (`cert.crt`, `cert.key`), and launches the HTTPS server without crashing or failing to bind ports.

---

## 🚀 Key Fixes & Enhancements

### 1. Autonomous SSL Certificate Auto-Generation
* **Decoupled Module Imports**: `core/services/ssl_service.py` safely resolves `BASE_DIR` even when imported prior to Django settings configuration, allowing zero-dependency certificate verification during early bootstrap.
* **Cascading Generation Strategy**:
  1. Python `cryptography` in-memory generator.
  2. Windows native PowerShell .NET in-memory PKI.
  3. Pre-embedded 10-year verified fallback certificate and RSA private key.
* **Automatic Root Sync**: `ensure_ssl_certificates()` automatically copies generated certificates to root `cert.crt` and `cert.key`, guaranteeing that any CLI scripts or services expecting root certificates find them immediately.

### 2. Robust Web Server Startup & Background Daemon
* **`manage.py run_web`**: Explicitly calls `ssl_service.ensure_ssl_certificates()` and passes absolute verified paths for `cert_file` and `key_file` directly to `runserver_plus`, preventing `FileNotFoundError: cert.crt`.
* **`itms start` / `itms restart`**: Updated daemon launcher to default to port 443 with HTTPS protocol attribution in logs and console output.
* **Bootstrap Integration**: Added explicit SSL certificate verification as Step 7 of `scripts/bootstrap.py`.

---

## 🔒 Upgrade & Migration Instructions

To apply v1.0.8 on your system:

```bash
itms update
```

Or via git:

```bash
git pull --rebase
itms restart
```
