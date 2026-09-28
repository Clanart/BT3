### Title
Semantically invalid `ThresholdKeys` (identity group key) deserialized via `ThresholdKeys::read` cause a guaranteed panic in downstream signing paths — (File: crypto/dkg/src/lib.rs)

### Summary
The CVE class is an unauthenticated-party-reachable crash: crafted/semantically-invalid data accepted during parsing later hits an assertion. The Serai analog is `ThresholdKeys::<C>::read`, which reads `n` verification shares with `Ciphersuite::read_G` (canonically-encoded but identity-allowed) and then lets `ThresholdKeys::new` derive `group_key` as the Lagrange-weighted sum of the shares for participants `1..=t` without checking the result is non-identity. An attacker can construct a serialized `ThresholdKeys` whose group key is the point at infinity. Any signing path that then needs the x-coordinate of the group key — e.g., the Bitcoin FROST `Hram`/`x()` helpers — panics on the point at infinity, giving a remote denial of service.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, the interpolation variant, the secret share, and then `n` verification shares using `<C as Ciphersuite>::read_G` (line 622). `Ciphersuite::read_G` only enforces a canonical encoding (crypto/ciphersuite/src/lib.rs:91-101); unlike `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) it does **not** reject the identity point. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:348-391) validates the *count* and *indexes* of verification shares, then computes

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(lines 376-378) — summing attacker-chosen points with known nonzero Lagrange coefficients, with no check that the resulting `group_key` is not identity and no consistency check tying the shares to `secret_share` at all.

Downstream, the BIP-340 `Hram` for `bitcoin_serai::crypto::Schnorr` calls `x(R)`/`x(A)` (networks/bitcoin/src/crypto.rs:59-66), and `x()` does `encoded.x().expect("point at infinity")` (networks/bitcoin/src/crypto.rs:13-16). `x_only` similarly panics (networks/bitcoin/src/crypto.rs:21-23), and `p2tr_script_buf`/`Scanner::new` (networks/bitcoin/src/wallet/mod.rs:80-86, 162-166) route through the same identity-unsafe conversion. So a `ThresholdKeys<Secp256k1>` with `group_key == identity` panics the moment `sign_share`/`verify` or scanning/tweaking touches it.

### Impact Explanation
Any component that feeds attacker-controlled bytes into `ThresholdKeys::read` (an explicitly in-scope untrusted-read API) and then performs a signing, scanning, or key-derivation operation can be crashed remotely. This mirrors CVE-2018-5735: input accepted during parsing is semantically invalid and deterministically trips a panic/assertion in a later code path, denying service. Because the panic is deterministic once the malicious bytes are supplied, a single message suffices — no race or probabilistic condition is needed.

### Likelihood Explanation
Constructing the malicious payload is trivial algebra: pick arbitrary valid (non-identity) points for shares `2..=n`, then set share `1` to `λ_1^{-1} · (-Σ_{i=2}^{t} λ_i·V_i)` where `λ_i` are the publicly computable Lagrange coefficients for the set `{1..=t}`. All encodings remain canonical so every deserialization check passes. Exploitation requires only that the attacker reach the `ThresholdKeys::read` ingestion point plus one subsequent group-key use — both are public-input surfaces.

### Recommendation
- In `ThresholdKeys::new` (crypto/dkg/src/lib.rs), reject a computed `group_key` that `is_identity()`, and reject identity verification shares at input (use `Curve::read_G` semantics or an explicit `is_identity` check) so that deserialization cannot yield semantically invalid keys.
- Defensively, make `x()`/`x_only`/`needs_negation` and `p2tr_script_buf` return `Option`/`io::Error` instead of panicking on the point at infinity (networks/bitcoin/src/crypto.rs:13-23), matching the crate's stated goal of not panicking on adversarial input.

### Proof of Concept
```rust
// For Secp256k1 ThresholdKeys with t = n = 2, i = 1, Interpolation::Lagrange:
// 1. Choose arbitrary non-identity points V2.
// 2. Lagrange factor for participant 1 over set {1,2}: l1 = 2/(2-1) = 2; for 2: l2 = 1/(1-2) = -1.
//    Set V1 = -(l1^{-1} * l2 * V2) so that l1*V1 + l2*V2 == identity.
// 3. Serialize: ID || t=2 || n=2 || i=1 || variant 1 || secret_share || V1 || V2.
// 4. ThresholdKeys::<Secp256k1>::read(&mut bytes) succeeds; keys.group_key() == identity.
// 5. bitcoin_serai::crypto::Schnorr::new() machine -> sign_share() calls
//    Hram::hram(&nonce_sums[0][0], &params.group_key(), msg) which calls x(A)
//    -> panic!("point at infinity") at networks/bitcoin/src/crypto.rs:15.
```

Relevant code: `ThresholdKeys::read`/`new` at crypto/dkg/src/lib.rs:574-632 and 376-378; non-identity-rejecting `Curve::read_G` (not used here) at crypto/frost/src/curve/mod.rs:125-131; panicking `x()`/`Hram` at networks/bitcoin/src/crypto.rs:13-16 and 59-66.

Caveat: I verified the panic primitive (`x()` on infinity) and the missing validation in `ThresholdKeys::new`/`read`, but did not exhaustively confirm which production caller pipes untrusted bytes into `ThresholdKeys::read` (e.g., `GeneratedKeysDb::read_keys` in processor/src/key_gen.rs reads from local DB, not the network). If no network-reachable caller exists, severity drops accordingly; the finding stands as an unsafe deserialization invariant violation within the in-scope crates.