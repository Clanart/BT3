### Title
Crafted short/malformed message panics `SchnorrkelHram::hram`, crashing the signer - (File: crypto/schnorrkel/src/lib.rs)

### Summary
Analogous to the Ironic report (crafted input to a service endpoint crashes the service, CWE-770), `SchnorrkelHram::hram` performs unchecked slice indexing on the attacker-influenced message `m`. Any message shorter than 4 bytes, or whose embedded `ctx_len` field exceeds the actual message length, causes an out-of-bounds slice panic inside `sign_share`/`verify_share`/`verify`, aborting the signing process.

### Finding Description
`SchnorrkelHram::hram` parses the message as `ctx_len || ctx || msg` using raw indexing:

- `m[0 .. 4]` — panics if `m.len() < 4` (crypto/schnorrkel/src/lib.rs:43).
- `u32::from_le_bytes(...)` is then trusted as `ctx_len`, and `m[4 .. (4 + ctx_len)]` and `m[(4 + ctx_len) ..]` are sliced (crypto/schnorrkel/src/lib.rs:46). A `ctx_len` larger than `m.len() - 4` panics; `4 + ctx_len` can also overflow `usize` in release for large values it wraps differently, but primarily this is an indexing panic.

`hram` is invoked by `FrostSchnorr` (`IetfSchnorr`) inside `sign_share`, `verify_share`, and `verify` on the `msg` parameter — the message the threshold group is asked to sign. In the Serai flow, signable messages (plans/transactions) are derived from public inputs an unprivileged party can cause to be signed. The panic is not caught; it propagates up through `Algorithm::sign_share`/`verify_share` and crashes the processor thread handling the signature. Additionally, `PublicKey::from_bytes(...).unwrap()` at crypto/schnorrkel/src/lib.rs:49 unwraps conversions, and `Scalar::from_repr(...).unwrap()` at line 52 assumes the merlin challenge is canonical — fine internally, but the message-side panics are directly attacker-controlled.

### Impact Explanation
An unprivileged party who can influence a message routed through the Schnorrkel signing path (e.g., a substrate-side transaction payload shorter than 4 bytes, or a payload whose first 4 bytes declare a `ctx_len` beyond the buffer) causes a panic during `sign_share` or `verify_share`. This kills the signing attempt and, depending on the caller's panic handling, crashes the processor — a service-level denial of service matching the Ironic availability impact (CVSS A:L → Medium). No secret material is required or leaked.

### Likelihood Explanation
`msg` is a protocol-level input: anything asking the multisig to sign an arbitrary or malformed payload reaches `hram`. The code performs no length validation before indexing, so a malformed or sub-4-byte message reliably panics. The bug is deterministic and requires no cryptographic capability — only the ability to supply a message to the Schnorrkel algorithm path. Medium likelihood where such a path is exposed; the signature path for Ristretto-based substrates exists in this crate.

### Recommendation
Validate the message before indexing in `SchnorrkelHram::hram`: return a defined error (or a safe scalar) when `m.len() < 4` or `ctx_len > m.len() - 4`, instead of panicking. Also replace `.unwrap()` on `m[0..4].try_into()`-adjacent length parsing with checked arithmetic (`checked_add` for `4 + ctx_len`). Ideally `hram` should not panic on untrusted input; document and enforce a minimum message length at the `Schnorrkel` algorithm boundary if the format is intentional.

### Proof of Concept
- Trigger via `sign_share`/`verify_share` on `Schnorr`/`Schnorrkel` with `msg` of length 0–3 → panic at `m[0 .. 4]` in crypto/schnorrkel/src/lib.rs:43.
- Or `msg` = `b"\xff\xff\xff\xff" + short_payload` (ctx_len = 0xFFFFFFFF) → panic at `m[4 .. (4 + ctx_len)]` in crypto/schnorrkel/src/lib.rs:46 due to out-of-bounds/overflowing slice range.

Citations: crypto/schnorrkel/src/lib.rs:41-52 (`hram` body), crypto/schnorrkel/src/lib.rs:56-62 (`Schnorrkel` wiring into `FrostSchnorr`).