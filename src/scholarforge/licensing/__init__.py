"""Subscription licensing (ScholarForge Pro, $20/month), separated from the scientific core.

* The core engine never imports this package; only the CLI/MCP adapters consult the gate.
* Licenses are Ed25519-signed tokens verified offline; a configurable grace period covers
  temporary loss of connectivity.
* Only the license key, a hashed device id and the app version are ever sent to the license
  server. Book content never leaves the user's machine.
* Local software cannot be made tamper-proof; this module does not pretend otherwise.
"""
