# Task295A — T-Invest Russian Trusted CA TLS Foundation v1

## Purpose and root cause

Task295's token-free transport diagnostic isolated the connection failure to
TLS certificate trust: DNS and TCP connectivity succeeded, but certificate
verification rejected a self-signed certificate in the presented chain. T-Bank
documents the Russian National Certification Authority certificates as a
requirement for T-Invest connectivity. This task establishes the trust material
at backend image build time and makes HTTPX use the resulting system bundle
explicitly. It does not make an authenticated request or assert token validity.

## Official certificate provenance

The Gosuslugi certificate page links the following public DER certificates
from its official download host. These exact files were verified with OpenSSL,
then converted deterministically to PEM for the repository:

| Artifact | Source URL | Subject | Issuer | Serial | Validity (UTC) | Certificate SHA-256 | PEM file SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `russian_trusted_root_ca.crt` | <https://gu-st.ru/content/downloads/Russian_Trusted_Root_CA.cer> | `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA` | Same as subject | `1000` | 2022-03-01 21:04:15 to 2032-02-27 21:04:15 | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` | `AA800EF345422D6158C6FAFE1C06C429DBDA21C3DF4BB1CCB45A920EC1111399` |
| `russian_trusted_sub_ca.crt` | <https://gu-st.ru/content/downloads/Russian_Trusted_Sub_CA.cer> | `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Sub CA` | `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA` | `1002` | 2022-03-02 11:25:19 to 2027-03-06 11:25:19 | `BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B` | `FD52C38348EED5F01C2EE3FB364A1FDE01D7C5C850BB29A645570106CC415B80` |

The root is self-issued and passed self-signature verification. The Sub CA
chains to the root (`openssl verify -CAfile russian_trusted_root_ca.crt
russian_trusted_sub_ca.crt` returned `OK`). The Sub CA expiry is approaching
relative to this task date; certificate refresh requires a separately reviewed
update using the then-current official source. The separately published
`Russian Trusted Sub CA 2024` is not substituted or bundled in this two-file
contract.

## Docker system trust

The backend image explicitly installs Debian `ca-certificates` and OpenSSL,
copies both PEM artifacts to `/usr/local/share/ca-certificates/`, and runs
`update-ca-certificates` during image build. Build checks require the normal
`/etc/ssl/certs/ca-certificates.crt` bundle to be nonempty, verify both added
certificates against the generated system bundle, verify the root's
self-signature and the Sub-to-Root chain, and assert that the resulting Python
SSL context still contains a broad CA set. The public Debian Web PKI is
augmented, not replaced. Certificates are not installed at container startup.

## HTTPX TLS behavior

When Task295 creates its own HTTPX client, `_build_ssl_context()` loads
`/etc/ssl/certs/ca-certificates.crt` explicitly and requires both
`ssl.CERT_REQUIRED` and hostname checking. `trust_env=False` and
`follow_redirects=False` remain in effect. Missing or invalid bundle
configuration raises a fixed, sanitized initialization error without exposing
host filesystem details. An injected `httpx.Client` remains entirely caller
owned; Task295 does not replace its transport or TLS settings.

No verification bypass, warning suppression, global SSL monkey-patch, dynamic
certificate append, environment proxy use, retry, or alternate transport is
introduced.

## Tests and image verification

Offline tests pin both certificate fingerprints, parse the PEM using Python's
SSL implementation, check Dockerfile trust-store construction, assert the
explicit TLS-context contract and fail-closed missing-bundle behavior, and
statically reject TLS bypasses in the production client. Where OpenSSL is
available, focused tests also verify the certificate metadata and chain. The
Dockerfile repeats the critical chain and system-bundle checks at build time.
The existing Task295 suite continues to use only synthetic tokens and
`httpx.MockTransport`.

## Scope, safety, and handoff

There are no schema, migration, database, persistence, API, frontend, MOEX,
M3, strategy, or risk-engine changes. No live T-Invest request, real token,
account/portfolio/order call, production or VDS access, deployment, or Shadow
run is performed. The handoff is independent Task295A review and exact-commit
CI, followed by a separately controlled backend deployment and token-free TLS
probe before any real token is re-entered.

```text
TASK_ID=Task295A
ROOT_CAUSE=RUSSIAN_TRUSTED_CA_NOT_TRUSTED
NETWORK_CONNECTIVITY_PROVEN=true
DNS_PROVEN=true
TCP_443_PROVEN=true
TLS_VERIFY_PRE_FIX=false
RUSSIAN_TRUSTED_ROOT_CA_BUNDLED=true
RUSSIAN_TRUSTED_SUB_CA_BUNDLED=true
CERTIFICATE_SOURCE_OFFICIAL=true
CERTIFICATE_FINGERPRINTS_PINNED=true
SYSTEM_CA_STORE_AUGMENTED=true
PUBLIC_WEB_PKI_PRESERVED=true
TINVEST_CA_INSTALLED_AT_BUILD=true
HTTPX_EXPLICIT_SSL_CONTEXT=true
HTTPX_TRUST_ENV=false
TLS_VERIFY=true
TLS_BYPASS=false
LIVE_TINVEST_REQUEST=false
REAL_TOKEN_USED=false
DATABASE_CHANGED=false
MIGRATION_ADDED=false
BROKER_WRITE_SURFACE=false
PRODUCTION_ACCESSED=false
SHADOW_STARTED=false
TASK296_STARTED=false
```
