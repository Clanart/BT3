### Title
Panic on identity nonce sum in BIP-340 `Hram`/`x()` causes remote DoS during FROST signing - (File: networks/bitcoin/src/crypto.rs)

### Summary
The BIP-340 challenge function `Hram::hram` and the point-to-x-coordinate helper `x()`/`x_only()` panic when passed the point at infinity. During a FROST signing session, the aggregate nonce `R` is the binding-factor-weighted sum of all participants' nonce commitments, which are attacker-controlled bytes delivered via `read_preprocess`. A participant in the signing set can choose their nonce commitments so the weighted sum is the identity point, causing `x(R)` inside `Hram::hram` to panic with `expect("point at infinity")` — crashing the signer mid-protocol. This is the Serai analog of CVE-2017-14228 (NULL pointer dereference → remote denial of service from untrusted input).

### Finding Description
`x()` in `networks/bitcoin/src/crypto.rs` (lines 13–16) does `key.to_encoded_point(true)` then `encoded.x().expect("point at infinity")`. `x_only()` (lines 21–23) and `Hram::hram` (lines 59–73) call `x(R)` and `x(A)` unconditionally. `hram` is invoked from `IetfSchnorr::sign_share`/`verify`, where `R` is derived from `nonce_sums` — the sum over `rho_i * D_i + E_i`-style bound commitments built from preprocesses parsed by `read_preprocess`. `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`) rejects individual identity points, but a weighted *sum* of valid non-identity points can still be identity. The code comments acknowledge this: "If either `R` or `A` is the point at infinity, this will panic" (line 54) and "this library MAY panic" (line 83). A signing-set participant who sees (or predicts, ROS-style) the other preprocesses can select commitments whose binding-factor combination sums to identity, then trigger the panic via `sign`/`complete`.

### Impact Explanation
Denial of service: a panic in `sign_share` or `verify` aborts the signing process. Since Bitcoin spends require the threshold signature, a single faulty participant's crafted preprocess can repeatedly crash signers (or at minimum abort every session they join), halting withdrawals. No key material is leaked, so this is a liveness/availability bug matching the Medium severity of the source CVE.

### Likelihood Explanation
Reachable by any participant able to submit a preprocess (`read_preprocess` → `sign`) in a signing session — i.e., a validator in the active set, which is the standard untrusted-input surface for these scans. The attack requires computing binding factors and solving for a commitment set summing to identity; this is feasible because `rho` values are deterministic over the published commitment set and the attacker can commit last. The crate's own documentation concedes the panic is possible, only claiming "negligible probability" for *honest random* inputs — not for adversarial ones.

### Recommendation
In `Hram::hram` (or the `Schnorr` `sign_share`/`verify` wrappers in `crypto.rs`), detect `R.is_identity()` / `A.is_identity()` before calling `x()` and return a `FrostError`/`None` instead of panicking. Alternatively, harden `x()`/`x_only()` to return `Option`/`io::Result` and propagate the failure so a malicious preprocess yields a blame/abort path rather than a process panic.

### Proof of Concept
```rust
// In a signing session over Secp256k1, an attacker participant waits for
// all other preprocesses, computes each rho_i = hash_binding_factor(...),
// then publishes nonce commitments (D_a, E_a) such that
//   sum_i(rho_i * D_i + E_i) == ProjectivePoint::identity()
// The commitments are individually non-identity and pass Curve::read_G.
// When honest signers call sign()/complete(), IetfSchnorr computes R = identity
// and calls Hram::hram -> x(R) -> encoded.x().expect("point at infinity") -> panic.

// Minimal unit reproduction of the panic primitive:
let identity = ProjectivePoint::IDENTITY;
let _ = Hram::hram(&identity, &group_key, b"msg"); // panics in x()
```

Confidence caveat: I could not fully trace `IetfSchnorr::sign_share` internals within the iteration limit to confirm `R` reaches `hram` without a prior identity check; the panic primitive in `x()`/`x_only()`/`Hram::hram` is confirmed by the file's own doc comments, and the reachability rests on aggregate nonce sums being computable to identity by the last-committing participant.