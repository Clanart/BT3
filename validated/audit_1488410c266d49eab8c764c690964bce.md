### Title
Malicious preprocess with identity nonce commitments panics honest signers (DoS) - (File: crypto/frost/src/nonce.rs, networks/bitcoin/src/crypto.rs)

### Summary
A participant in a FROST signing session can submit a `Preprocess` whose nonce commitments are the point at infinity. `Commitments::read`/`GeneratorCommitments::read` only enforce canonical encoding via `C::read_G`, which accepts the identity point. When `AlgorithmSignMachine::sign` aggregates the bound nonces, the resulting `R` is identity, and the BIP-340 `Hram::hram` / `x()` helper panics on `"point at infinity"`, crashing the honest signer — a remotely triggerable denial of service from attacker-controlled bytes fed to `read_preprocess`.

### Finding Description
`Commitments::read` (nonce.rs:133) reads `generators.len()` `GeneratorCommitments` pairs, each via `GeneratorCommitments::read` (nonce.rs:34), which calls `Curve::read_G` twice. `read_G` (crypto/ciphersuite/src/lib.rs:91-100) checks only that the point decodes and re-encodes canonically; it does not reject the identity. For secp256k1, the SEC1 infinity encoding is canonical, so `D = identity, E = identity` parses cleanly.

In `sign` (crypto/frost/src/sign.rs:283), these commitments enter `BindingFactor` and `B.nonces` computes `R = D + rho*E` (nonce.rs:194-208), yielding `R = identity` deterministically (no negligible probability involved — the attacker simply sets both commitments to infinity). `sign_share` then evaluates `Hram::hram(R, A, m)` (networks/bitcoin/src/crypto.rs:59), which calls `x(R)`:

```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

`encoded.x()` returns `None` for infinity → `expect` panics. The same panic occurs in `verify` (crypto.rs:145-149) during `complete` via `needs_negation`/`x_only` on an identity `R`.

The comment at crypto.rs:78-80 claims infinity nonces have only "negligible probability... even with malicious participants present", which is incorrect — a malicious participant crafts them deterministically.

### Impact Explanation
Any single participant in a threshold signing session can abort/crash every honest signer's `sign()` call (and `complete()` via `verify`) by broadcasting a preprocess whose nonce commitments are identity. If the signing code runs in-process without panic isolation, this is a full node crash; even caught, it reliably aborts the signing session without a `FrostError`, bypassing the blame mechanism (`InvalidShare`) since the panic precedes share verification. This is the analog of the crafted-input NULL-deref DoS in the report: untrusted bytes → deserialization accepts them → downstream dereference/panic.

### Likelihood Explanation
Requires only being one preprocessing participant — the exact position the FROST protocol assumes may be malicious. The payload is trivial: a preprocess message of all-infinity point encodings. No collusion or special timing needed; deterministic panic on every honest signer that reads the preprocess and calls `sign`.

### Recommendation
Reject the identity point in `GeneratorCommitments::read` (and generally where group elements represent nonces/keys), e.g.:

```rust
let point = <C as Curve>::read_G(reader)?;
if bool::from(point.is_identity()) {
  Err(io::Error::other("identity point in nonce commitments"))?;
}
```

Alternatively/defense-in-depth, make `BindingFactor::nonces` return `io::Result`/`Option` and propagate a `FrostError::InvalidCommitments` when any aggregate `R` is identity, instead of letting it reach `hram`.

### Proof of Concept
1. Honest signer i runs `AlgorithmMachine::new(Schnorr::new(), keys)` (Secp256k1), calls `preprocess`, then `read_preprocess` on the attacker's message.
2. Attacker's preprocess bytes: for each nonce/generator, two secp256k1 identity encodings (canonical SEC1 infinity), plus empty addendum.
3. `Commitments::read` succeeds (identity is canonical under `read_G`).
4. Attacker's participant index is inserted into `B`; `B.nonces` computes `R_n = identity + rho*identity = identity`.
5. Honest signer calls `sign(preprocesses, msg)` → `Schnorr::sign_share` → `IetfSchnorr` calls `Hram::hram(&R, &A, m)` → `x(R)` → `expect("point at infinity")` panics.

Uncertain detail: whether `read_G` for the Secp256k1 curve (kp256/k256 `GroupEncoding`) accepts the identity encoding — k256's `from_bytes` accepts SEC1 infinity (`0x00` tag), and `to_bytes` round-trips it canonically, so the canonicality check passes. If a custom wrapper rejected infinity at decode, the equivalent path is `E = -D / rho` style cancellation via `multiexp_vartime` in `BindingFactor::nonces`, which can still produce identity `R` from non-identity commitments, triggering the same panic — so the identity-decode question does not affect reachability of the bug.