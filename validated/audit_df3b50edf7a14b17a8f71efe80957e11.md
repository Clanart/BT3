### Title
Panic on identity/invalid group key in `Schnorrkel::verify` turns a malformed `ThresholdKeys` deserialization into a signing-path DoS - (File: crypto/schnorrkel/src/lib.rs)

### Summary
`AlgorithmSignatureMachine::complete` feeds the deserialized group key into `Schnorrkel::verify`, which `unwrap()`s `PublicKey::from_bytes(&group_key.to_bytes())` and `Signature::from_bytes`. An identity or otherwise invalid group key — producible by feeding attacker-controlled bytes to `ThresholdKeys::read` whose `verification_shares` sum to the identity — makes `verify` panic instead of returning `None`, crashing the signing participant. This mirrors CVE-2021-35636's class: an attacker-controlled input reaching code that assumes internal consistency, yielding a repeatable crash (complete availability loss for that signing instance).

### Finding Description
- `ThresholdKeys::<C>::read` (crypto/dkg/src/lib.rs:574-632) parses `t`, `n`, `i`, the interpolation variant, `secret_share`, and `n` `verification_shares` purely from the reader, then calls `ThresholdKeys::new`. It performs no check that the resulting `group_key()` (derived from the first `t` verification shares) is non-identity or on the intended key. An attacker supplying `verification_shares` such that `sum(shares[0..t]) = G::identity()` — e.g., `v1 = -v2` for `t = 2` — produces a `ThresholdKeys` whose `group_key` is the point at infinity.
- When that key set is used for signing, `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:465) calls `self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum)`.
- `Schnorrkel::verify` (crypto/schnorrkel/src/lib.rs:138-145) does `PublicKey::from_bytes(&group_key.to_bytes()).unwrap()` — the identity point is not a valid `schnorrkel` `PublicKey`, so this `unwrap()` panics. The same structure exists for the Bitcoin `Schnorr` algorithm, where `Hram::hram` calls `x(A)` which `expect()`s a non-infinity point (networks/bitcoin/src/crypto.rs:15, 59-67), so an identity group key or identity aggregate nonce `R` also panics there.

### Impact Explanation
Any code path that accepts untrusted `ThresholdKeys` bytes (resumption/recovery blobs, fetched key material) or aggregates attacker-influenced group keys can be crashed by a single crafted serialization. Because the panic is deterministic given the crafted input, an attacker can repeatedly crash every node that loads the keys — a complete, repeatable denial of service of the threshold-signing component, matching the Medium severity of the reference CVE (availability-only impact, no secret leakage required).

### Likelihood Explanation
The reachability prerequisite is an attacker who can supply or corrupt the serialized `ThresholdKeys` fed to `ThresholdKeys::read`, or contribute verification shares influencing `group_key`. Serialization integrity is delegated to callers, and `ThresholdKeys::read` explicitly tolerates "semantically invalid FrostKeys" only as far as producing `FrostError::InternalError` (crypto/frost/src/sign.rs:492-494) — but the `Schnorrkel` path panics before that error can be returned, so the intended graceful failure does not occur. Requiring attacker influence over key material keeps this below High.

### Recommendation
- In `ThresholdKeys::new` / `ThresholdKeys::read`, reject group keys and verification shares equal to the identity (return an `io::Error`/`FrostError`).
- In `Schnorrkel::verify` and `bitcoin::crypto::Hram`/`x_only`, replace `unwrap()`/`expect()` on `PublicKey::from_bytes` / `x()` with `Option`-returning checks so an invalid point yields `None` (verification failure) instead of a panic.

### Proof of Concept
```rust
// Crypto/dkg deserialization produces an identity group_key from crafted bytes:
let mut serialized = vec![];
serialized.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
serialized.extend(Ristretto::ID);
serialized.extend(2u16.to_le_bytes()); // t = 2
serialized.extend(2u16.to_le_bytes()); // n = 2
serialized.extend(1u16.to_le_bytes()); // i = 1
serialized.push(1);                    // Interpolation::Lagrange
serialized.extend(Scalar::ONE.to_repr().as_ref()); // secret_share
let p = RistrettoPoint::generator();
serialized.extend(p.to_bytes().as_ref());          // share[0] = G
serialized.extend((-p).to_bytes().as_ref());       // share[1] = -G
// group_key = shares[0..t] combination resolves to identity
let keys = ThresholdKeys::<Ristretto>::read(&mut serialized.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

// Signing then reaches:
//   complete() -> Schnorrkel::verify(group_key=identity, ...)
//   -> PublicKey::from_bytes(&group_key.to_bytes()).unwrap()  // PANIC
```