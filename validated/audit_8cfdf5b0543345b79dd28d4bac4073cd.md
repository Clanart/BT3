### Title
Deserialized `ThresholdKeys` with identity group key causes panic during Schnorr signing - ([File: networks/bitcoin/src/crypto.rs])

### Summary
The analog of CVE-2019-1301 (denial of service via improper handling of attacker-controlled input) in Serai's in-scope code is a reachable panic: `ThresholdKeys::read` accepts arbitrary verification-share points without checking that the resulting group key is not the identity, and the Bitcoin `Schnorr` algorithm's challenge hash `Hram::hram` unconditionally calls `x()`/`x_only()` helpers which `.expect("point at infinity")` — panicking when `A` (the group key) is the point at infinity. An unprivileged party who feeds crafted bytes to `ThresholdKeys::read` can therefore crash the signing process.

### Finding Description
`ThresholdKeys::read` in `crypto/dkg/src/lib.rs` reads `n` verification-share points via `<C as Ciphersuite>::read_G` (crypto/dkg/src/lib.rs:620-623) and passes them to `ThresholdKeys::new`. `ThresholdKeys::new` computes `group_key` as a Lagrange-weighted sum of those shares (crypto/dkg/src/lib.rs:376-378) with no identity check — notably, `Ciphersuite::read_G` does not reject identity (only `Curve::read_G` does, crypto/frost/src/curve/mod.rs:125-131), and `ThresholdKeys::new` only validates the share count and participant indexes (crypto/dkg/src/lib.rs:355-365).

An attacker crafting serialized `ThresholdKeys` bytes can choose verification shares so that `sum(verification_shares[i] * interpolation_factor(i, 1..=t))` equals the identity element. The Lagrange factors are publicly computable scalars, so constructing such points is trivial algebra (pick all shares but one arbitrarily, solve for the last).

When these keys are used with `bitcoin-serai`'s `Schnorr` algorithm, `IetfSchnorr::sign_share`/`verify` invoke `Hram::hram(R, A, m)` (networks/bitcoin/src/crypto.rs:59-73), which calls `x(R)` and `x(A)`. `x` does `key.to_encoded_point(true)` then `.x().expect("point at infinity")` (networks/bitcoin/src/crypto.rs:13-16), panicking on the identity point. The panic occurs inside `sign_share` during `machine.sign(...)` in `AlgorithmSignMachine::sign` (crypto/frost/src/sign.rs:283-411), before any share is produced.

### Impact Explanation
A single crafted `ThresholdKeys` blob causes a deterministic panic (abort of the signing task/process) whenever the key is used to sign, permanently denying service for that multisig instance until the poisoned key material is discarded. This maps the advisory's class — improper input handling producing denial of service — onto Serai: untrusted bytes → unchecked algebraic invariant (non-identity group key) → panic in `x()`/`x_only()`.

### Likelihood Explanation
`ThresholdKeys::read` is explicitly a reachable deserialization entry point for untrusted bytes. The crafted input requires only solving a public linear relation over group elements — no hash grinding, no secret knowledge — so construction is easy for any party able to supply the serialized key bytes. It is less severe than the remote-network trigger of the original advisory (the attacker must be able to feed `ThresholdKeys` bytes to the victim), so the analog is Medium rather than High.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:376-390), reject a computed `group_key` that `is_identity()`; additionally consider rejecting identity verification shares at `ThresholdKeys::read`. Alternatively or in addition, make `x()`/`x_only()`/`Hram::hram` in networks/bitcoin/src/crypto.rs return an error/`Option` rather than panicking on the point at infinity, so invalid key material degrades to a `FrostError` instead of crashing.

### Proof of Concept
```rust
// Attacker crafts ThresholdKeys bytes for Secp256k1, t = n = 2, i = 1,
// Interpolation::Lagrange (tag byte 1), arbitrary secret_share scalar.
// For participants 1..=2, Lagrange factors are l1 = 2*(2-1)^-1 ... (publicly computable).
// Set verification_shares[1] = G, verification_shares[2] = -(l1/l2) * G so that
// l1*V1 + l2*V2 == identity. Then:

let keys = ThresholdKeys::<Secp256k1>::read(&mut crafted_bytes).unwrap(); // succeeds
let machine = AlgorithmMachine::new(Schnorr::new(), keys);
// ... obtain a preprocess ...
// machine.sign(preprocesses, msg) -> Hram::hram(R, identity, msg)
//   -> x(&identity) -> encoded.x().expect("point at infinity") -> PANIC
```

Uncertainty note: I verified `Ciphersuite::read_G` does not reject identity only indirectly (via `Curve::read_G` adding the check at crypto/frost/src/curve/mod.rs:125-131); if the underlying `Ciphersuite::read_G` implementations happen to reject identity themselves, the attack instead needs verification shares that are non-identity but whose weighted sum is identity — which remains trivially constructible, so the finding stands either way.