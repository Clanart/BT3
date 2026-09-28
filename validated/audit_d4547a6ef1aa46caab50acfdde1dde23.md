### Title
Remote denial of service: attacker-controlled preprocess points force nonce sum to the point at infinity, panicking BIP-340 `Hram::hram` / `x_only` during `sign` - (File: networks/bitcoin/src/crypto.rs)

### Summary
CVE-2017-14862 is a crash-on-malformed-input bug: `Exiv2::DataValue::read` dereferences an invalid address on untrusted bytes, causing a segmentation fault. The Serai analog is a reachable panic on attacker-controlled group elements. In the BIP-340 FROST algorithm (`Schnorr` in `networks/bitcoin/src/crypto.rs`), the challenge function `Hram::hram` calls `x(R)`, which executes `encoded.x().expect("point at infinity")` and panics if the aggregate nonce commitment `R` is the point at infinity. `R` (`nonce_sums[0][0]`) is the sum of `D_j + rho_j * E_j` over all signers' preprocess commitments, which are parsed verbatim from untrusted bytes by `read_preprocess` → `Commitments::read` → `GeneratorCommitments::read` → `Curve::read_G` — `read_G` accepts any canonically encoded point, including the identity. No identity/nonces-zero check exists in `NonceCommitments::read` or `Commitments::read`, and `AlgorithmSignMachine::sign` validates only participant indexes, not the committed points.

### Finding Description
- `crypto/frost/src/nonce.rs:34-36` — `GeneratorCommitments::read` reads two arbitrary points with no identity rejection.
- `crypto/frost/src/sign.rs:276-281` — `read_preprocess` feeds attacker bytes into `Commitments::read` and `read_addendum`; the only validation in `sign` (`crypto/frost/src/sign.rs:290-313`) is signer-set checks (`included` bounds and duplicates).
- `networks/bitcoin/src/crypto.rs:13-16` — `fn x` panics via `.expect("point at infinity")` on the identity; `networks/bitcoin/src/crypto.rs:59-73` — `Hram::hram` calls `x(R)` on the aggregate nonce point during `Schnorr::sign_share` (`crypto/frost/src/algorithm.rs:201-211`).

A signer (or anyone who can submit a preprocess blob for a signing session, e.g., a cosigner in a Bitcoin multisig sign operation) computes the other participants' contribution `S = sum_{j≠evil}(D_j + rho_j * E_j)` from the broadcast preprocesses, then submits a preprocess with `E_evil = identity` (making its own `rho * E` term zero regardless of the hash-derived `rho`) and `D_evil = -S`. The binding factor `rho` depends on the preprocess set, but the `E = identity` trick removes the circular dependency. The aggregate `nonce_sums[0][0]` then equals the point at infinity, and `sign_share` → `hram` → `x()` panics, crashing the signing process.

The same panic is reachable on the verification path: `Schnorr::verify` and `Schnorr::verify_share` (`networks/bitcoin/src/crypto.rs:139-159`) index `nonces[0][0]` and route through the same `hram`/`x` code path, so a crafted preprocess set also crashes `complete` during share aggregation.

### Impact Explanation
An unprivileged participant in a FROST signing session — whose only capability is submitting preprocess bytes to `read_preprocess` — can deterministically crash every honest signer that attempts `sign`/`complete` for that session. This aborts the Bitcoin signing protocol (panic, not a catchable `FrostError`), denying service to the multisig and potentially stalling fund movement. This mirrors the CVE's denial-of-service-by-crafted-input class. Severity: Medium (availability loss, requires participation in a signing session, no secret leakage). Caveat: the rules exclude "malicious-validator" analogs; this is reported because the accepted input surface explicitly includes "untrusted bytes fed to ... `read_preprocess`", and the crash vector is the deserialization acceptance of identity points rather than validator misbehavior per se.

### Likelihood Explanation
Deterministic, not probabilistic. The attacker needs only: (1) observe the other signers' preprocesses (publicly broadcast), (2) submit `D = -sum(others' D + rho*E)`, `E = identity`. Both points are canonically encodable and pass `read_G`'s canonical check (`crypto/ciphersuite/src/lib.rs:91-101`). The doc comment at `crypto.rs:78-80` claims identity nonces have "negligible probability... even with malicious participants present" — this is incorrect because `rho` binds the preprocess set but the attacker can nullify their own `rho` term with `E = identity`.

### Recommendation
Reject the point at infinity (and any non-prime-subgroup / low-order point where applicable) in `GeneratorCommitments::read` or in `NonceCommitments::read`, and additionally check that each signer's combined commitment `D + rho*E` and the final aggregate `nonce_sums[*][*]` are non-identity before calling `sign_share`/`verify_share`/`verify`, returning `FrostError::InvalidPreprocess` instead of panicking. More broadly, `Hram::hram` should not panic on adversary-influenced inputs — propagate an error rather than `expect` in `x`/`x_only`.

### Proof of Concept
```rust
// Attacker is the last to submit a preprocess for a Bitcoin (Secp256k1) FROST sign.
// Given all other preprocesses, compute the partial aggregate nonce commitment:
//   S = sum_j (D_j + rho_j * E_j)   // rho_j computable from the preprocess set + msg
// Attacker preprocess encodes:
//   D_evil = -S                     // any canonical point encoding; read_G accepts it
//   E_evil = Secp256k1 identity     // rho_evil * E_evil == identity regardless of rho
//
// Victim's SignMachine::read_preprocess accepts both points (no identity check in
// crypto/frost/src/nonce.rs GeneratorCommitments::read / Commitments::read).
// In sign(), nonce_sums[0][0] = S + (D_evil + rho_evil * E_evil) = identity.
// Schnorr::sign_share -> Hram::hram(&identity, &group_key, msg)
//   -> x(&identity) -> encoded.x().expect("point at infinity")  // PANIC
```
Relevant code: `networks/bitcoin/src/crypto.rs:13-23` (panicking `x`/`x_only`), `networks/bitcoin/src/crypto.rs:59-73` (`hram`), `crypto/frost/src/nonce.rs:34-80` (unchecked point reads), `crypto/frost/src/sign.rs:276-313` (`read_preprocess`/`sign` validating only participant indexes), `crypto/frost/src/algorithm.rs:201-230` (`sign_share`/`verify`/`verify_share` consuming `nonce_sums[0][0]`).