"""Root signing (Lockdown Path).

Pluggable ``Signer`` abstraction over the published daily Merkle root.
Mirrors the HashiCorp Vault precedent for OSS vs. enterprise tiering:

- **OSS (this file)**
    * ``LocalSigner``  -- HMAC over a long-lived root key on disk;
      simple, hash-equivalent to GPG/age for audit but with no
      external dependency.
    * ``GpgSigner``    -- detached GPG signature; requires
      ``python-gnupg``.
    * ``AgeSigner``    -- detached ``age`` signature; requires the
      ``age`` binary in ``$PATH``.
    * ``KmsSigner``    -- envelope-sign via cloud KMS (AWS / GCP /
      Azure). KMS keys are software-protected; the keys are managed by
      a cloud KMS service.

- **<brand> Cloud (stub here -- not implemented in OSS)**
    * ``CloudHsmSigner`` -- AWS CloudHSM / GCP Cloud HSM / Azure
      Dedicated HSM, FIPS 140-2 Level 3+ hardware-protected keys.

This split mirrors HashiCorp Vault: cloud-KMS auto-unseal is free,
HSM-rooted signing is the enterprise edition. Banks that need
FIPS 140-2 L3+ pay for it; everyone else gets software-protected
chain-of-custody for free.

The production flow:

    fast path  ->  daily Merkle aggregation  ->  Signer.sign(root)
       |                                              |
       v                                              v
    chain.py + merkle.py                       sign.py (this file)
"""

from __future__ import annotations

import abc
import base64
import hashlib
import hmac
import logging
import os
import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Type

from mrm.evidence.merkle import DailyMerkleRoot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signer ABC + registry
# ---------------------------------------------------------------------------

class Signer(abc.ABC):
    """Sign and verify ``DailyMerkleRoot`` artefacts.

    Implementations must be deterministic for verification: given the
    same signed_bytes and the same key material, ``verify`` must
    return True. The signature payload itself may be non-deterministic
    (e.g. GPG randomised k); that's fine as long as ``verify``
    succeeds.
    """

    #: Stable short name used in YAML config + the signed artefact.
    name: ClassVar[str] = ""

    #: Whether this signer requires an HSM. False in OSS; the cloud
    #: subclass overrides to True. The OSS surface raises early if a
    #: caller asks for an HSM-only signer.
    requires_hsm: ClassVar[bool] = False

    @abc.abstractmethod
    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:
        """Return a new DailyMerkleRoot with ``signature`` populated."""

    @abc.abstractmethod
    def verify(self, root: DailyMerkleRoot) -> bool:
        """Return True iff ``root.signature`` is valid for ``root``."""

    def sign_bytes(self, data: bytes) -> str:
        """Sign arbitrary bytes and return the signature as a hex/base64 string.

        Default implementation wraps ``sign()`` by building a
        ``DailyMerkleRoot`` proxy whose ``signed_bytes()`` returns *data*,
        signs it, and returns the ``signature`` field.  Subclasses may
        override for efficiency.
        """
        from dataclasses import replace

        proxy = DailyMerkleRoot(
            epoch="__proxy__",
            root_hash="00" * 32,
            leaf_count=0,
            sessions=[],
        )
        # Monkey-patch signed_bytes so the signer hashes *data* instead.
        proxy.signed_bytes = lambda: data  # type: ignore[assignment]
        signed = self.sign(proxy)
        return signed.signature or ""

    def describe(self) -> str:
        return f"{self.name} (HSM={self.requires_hsm})"


_REGISTRY: Dict[str, Type[Signer]] = {}


def register_signer(cls: Type[Signer]) -> Type[Signer]:
    if not cls.name:
        raise ValueError(f"Signer {cls.__name__} must set .name")
    _REGISTRY[cls.name] = cls
    return cls


def get_signer_cls(name: str) -> Type[Signer]:
    if name not in _REGISTRY:
        raise KeyError(
            f"Signer '{name}' not registered. Known signers: "
            f"{sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_signers() -> Dict[str, Dict[str, Any]]:
    """For ``mrm evidence root list-signers``."""
    return {
        name: {"requires_hsm": cls.requires_hsm}
        for name, cls in sorted(_REGISTRY.items())
    }


# ---------------------------------------------------------------------------
# LocalSigner -- HMAC-SHA256 with a root key on disk
# ---------------------------------------------------------------------------

@register_signer
class LocalSigner(Signer):
    """HMAC-SHA256 over a long-lived root key in a file.

    Suitable for dev, CI, and air-gapped institutions that prefer
    HMAC-based root signing without GPG/age tooling. The root key file
    is created mode 0600 with 256 bits of entropy if missing.

    Not a substitute for an HSM-backed signer in regulated production;
    treat as a "GPG/age-equivalent for OSS users without GPG keys".
    """

    name: ClassVar[str] = "local"

    def __init__(self, key_path: Path) -> None:
        self.key_path = Path(key_path)
        self._key = self._load_or_create()

    def _load_or_create(self) -> bytes:
        if self.key_path.exists():
            data = self.key_path.read_bytes().strip()
            if len(data) >= 32:
                try:
                    return bytes.fromhex(data.decode("ascii"))
                except (UnicodeDecodeError, ValueError):
                    return data
        key = secrets.token_bytes(32)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.key_path.with_suffix(self.key_path.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(key.hex().encode("ascii"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.key_path)
        return key

    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:
        mac = hmac.new(self._key, root.signed_bytes(), hashlib.sha256).hexdigest()
        root.signature = mac
        root.signer = self.name
        return root

    def verify(self, root: DailyMerkleRoot) -> bool:
        if not root.signature:
            return False
        expected = hmac.new(self._key, root.signed_bytes(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, root.signature)


# ---------------------------------------------------------------------------
# GpgSigner -- detached GPG signature
# ---------------------------------------------------------------------------

@register_signer
class GpgSigner(Signer):
    """Detached GPG signature over the canonical signed bytes.

    Requires ``python-gnupg`` and a GPG key importable into the host
    keyring. The signature is stored base64-encoded so it can sit in
    JSON without escaping.
    """

    name: ClassVar[str] = "gpg"

    def __init__(self, key_id: str, passphrase: Optional[str] = None) -> None:
        try:
            import gnupg  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "GpgSigner requires python-gnupg: pip install python-gnupg"
            ) from exc
        self.key_id = key_id
        self.passphrase = passphrase

    def _gpg(self):
        import gnupg
        return gnupg.GPG()

    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:
        sig = self._gpg().sign(
            root.signed_bytes(),
            keyid=self.key_id,
            passphrase=self.passphrase,
            detach=True,
            clearsign=False,
            binary=True,
        )
        if not sig:
            raise RuntimeError(f"GPG signing failed: {getattr(sig, 'stderr', '')}")
        root.signature = base64.b64encode(bytes(sig.data)).decode("ascii")
        root.signer = self.name
        return root

    def verify(self, root: DailyMerkleRoot) -> bool:
        if not root.signature:
            return False
        sig_bytes = base64.b64decode(root.signature)
        with tempfile.NamedTemporaryFile(delete=False) as sig_fh:
            sig_fh.write(sig_bytes)
            sig_path = sig_fh.name
        try:
            verified = self._gpg().verify_data(sig_path, root.signed_bytes())
            return bool(verified)
        finally:
            Path(sig_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# AgeSigner -- minisign-style detached signature via the `age` binary
# ---------------------------------------------------------------------------

@register_signer
class AgeSigner(Signer):
    """Detached signature via the ``age`` binary.

    Strictly speaking ``age`` is encryption, not signing. We use it
    here as a convenient OSS alternative to GPG: produce an encrypted
    ciphertext over the signed bytes addressed to the holder of the
    private key. ``verify`` succeeds iff the holder can decrypt back
    to the original bytes.

    Banks that already have a signing key infrastructure should use
    ``GpgSigner`` or ``KmsSigner``. ``AgeSigner`` is here so users
    without GPG can still produce something cryptographically useful.
    """

    name: ClassVar[str] = "age"

    def __init__(self, recipient: str, identity_path: Path) -> None:
        self.recipient = recipient
        self.identity_path = Path(identity_path)
        if not _binary_exists("age"):
            raise FileNotFoundError(
                "`age` binary not found in PATH; install from "
                "https://github.com/FiloSottile/age"
            )

    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:
        proc = subprocess.run(
            ["age", "-r", self.recipient, "-a"],
            input=root.signed_bytes(),
            capture_output=True,
            check=True,
        )
        root.signature = base64.b64encode(proc.stdout).decode("ascii")
        root.signer = self.name
        return root

    def verify(self, root: DailyMerkleRoot) -> bool:
        if not root.signature:
            return False
        sig = base64.b64decode(root.signature)
        proc = subprocess.run(
            ["age", "-d", "-i", str(self.identity_path)],
            input=sig,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout == root.signed_bytes()


def _binary_exists(name: str) -> bool:
    from shutil import which
    return which(name) is not None


# ---------------------------------------------------------------------------
# KmsSigner -- envelope-sign via AWS KMS / GCP KMS / Azure Key Vault
# ---------------------------------------------------------------------------

@register_signer
class KmsSigner(Signer):
    """Cloud-KMS-backed signature.

    Maps to the dbt-Cloud / HashiCorp Vault precedent: cloud KMS sits in
    the OSS surface; only HSM (``CloudHsmSigner``, below) is gated to
    the paid tier.

    Currently supports AWS KMS via ``boto3``. The provider+key are
    referenced by URI, e.g. ``aws-kms://<region>/<key-id>``. Other
    providers (GCP KMS, Azure Key Vault) follow the same pattern --
    branch on the URI scheme and call the provider SDK.

    The provider SDKs are imported lazily so an OSS install does not
    require ``boto3``/``google-cloud-kms``.
    """

    name: ClassVar[str] = "kms"

    def __init__(self, key_uri: str, *, client: Optional[Any] = None) -> None:
        self.key_uri = key_uri
        self._client = client  # tests inject a fake; real impl builds it lazily

    # ----- provider plumbing ------------------------------------------

    def _parse_uri(self) -> Dict[str, str]:
        if "://" not in self.key_uri:
            raise ValueError(f"Bad KMS key URI: {self.key_uri!r}")
        scheme, rest = self.key_uri.split("://", 1)
        return {"scheme": scheme, "rest": rest}

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        parsed = self._parse_uri()
        if parsed["scheme"] == "aws-kms":
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "AWS KMS signer requires boto3: pip install boto3"
                ) from exc
            region, _, _ = parsed["rest"].partition("/")
            self._client = boto3.client("kms", region_name=region or None)
            return self._client
        raise NotImplementedError(
            f"KMS provider {parsed['scheme']!r} not implemented yet"
        )

    def _key_id(self) -> str:
        return self._parse_uri()["rest"].split("/", 1)[-1]

    # ----- Signer API --------------------------------------------------

    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:
        client = self._resolve_client()
        resp = client.sign(
            KeyId=self._key_id(),
            Message=root.signed_bytes(),
            MessageType="RAW",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        # boto3 returns bytes for Signature; the fake client also does.
        sig: bytes = resp["Signature"]
        root.signature = base64.b64encode(sig).decode("ascii")
        root.signer = self.name
        return root

    def verify(self, root: DailyMerkleRoot) -> bool:
        if not root.signature:
            return False
        client = self._resolve_client()
        resp = client.verify(
            KeyId=self._key_id(),
            Message=root.signed_bytes(),
            MessageType="RAW",
            Signature=base64.b64decode(root.signature),
            SigningAlgorithm="ECDSA_SHA_256",
        )
        return bool(resp.get("SignatureValid"))


# ---------------------------------------------------------------------------
# CloudHsmSigner -- the paid-tier stub (intentionally unimplemented in OSS)
# ---------------------------------------------------------------------------

@register_signer
class CloudHsmSigner(Signer):
    """FIPS 140-2 Level 3+ HSM-backed signing.

    This is the **paid-tier feature**. The OSS class is a stub that
    raises ``NotImplementedError`` if instantiated; ``<brand>`` Cloud
    provides the implementation against AWS CloudHSM / GCP Cloud HSM /
    Azure Dedicated HSM.

    Why this lives in OSS at all
    ----------------------------

    1. So the plug-point is documented and conformance test vectors
       can target it.
    2. So the CLI can present a clear "this feature is in the paid
       tier" message rather than an unknown-signer error.
    3. So a third-party integrator can implement an on-prem HSM
       signer (e.g. Thales Luna) by subclassing this and overriding
       ``sign`` / ``verify``.
    """

    name: ClassVar[str] = "cloud-hsm"
    requires_hsm: ClassVar[bool] = True

    PAID_TIER_MESSAGE = (
        "cloud-hsm signing requires <brand> Cloud or an on-prem HSM "
        "implementation. mrm-core ships the plug-point and conformance "
        "vectors; the FIPS 140-2 L3+ implementation is part of the "
        "paid tier. See docs/spec/evidence-vault-v1.md and "
        "STRATEGY.md (P15)."
    )

    def __init__(self, *_: Any, **__: Any) -> None:
        raise NotImplementedError(self.PAID_TIER_MESSAGE)

    def sign(self, root: DailyMerkleRoot) -> DailyMerkleRoot:  # pragma: no cover
        raise NotImplementedError(self.PAID_TIER_MESSAGE)

    def verify(self, root: DailyMerkleRoot) -> bool:  # pragma: no cover
        raise NotImplementedError(self.PAID_TIER_MESSAGE)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_signer(config: Dict[str, Any]) -> Signer:
    """Build a Signer from a config dict.

    ``config`` shape::

        { "name": "<signer-name>", ...signer-specific kwargs... }

    Raises ``KeyError`` for unknown signers and ``NotImplementedError``
    for the cloud-hsm stub.
    """
    cfg = dict(config)
    name = cfg.pop("name", None)
    if not name:
        raise ValueError("signer config must include a 'name' field")
    cls = get_signer_cls(name)
    return cls(**cfg)
