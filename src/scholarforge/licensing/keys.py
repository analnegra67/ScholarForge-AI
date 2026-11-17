"""Public verification keys compiled into a release (base64 raw Ed25519).

Empty in source checkouts: development builds run with ``license.enforcement = off``.
The release pipeline writes the production public key here; additional keys can be
supplied with ``SCHOLARFORGE_LICENSE_PUBKEYS`` (comma-separated) for key rotation.
"""

PUBLIC_KEYS: list[str] = []
