### Title
Crafted `ThresholdKeys` serialization accepted without any consistency check — attacker-controlled group key and secret share (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation mode, the secret share, and every participant's verification share entirely from attacker-supplied bytes, then hands them to `ThresholdKeys::new`, which performs no consistency validation whatsoever. The secret share is never checked against the holder's own verification share (`secret_share * G == verification_shares[i]`), identity verification shares are accepted because `C::read_G` (the `Ciphersuite` impl, not the identity-rejecting `Curve::read_G` in crypto/frost/src/curve/mod.rs:125-131) is used, and the resulting `group_key` is computed as a blind sum over the supplied shares. This is the analog of the arbitrary-file-upload class: attacker-uploaded content (a crafted serialized key file) is ingested and executed on without validation, producing keys whose group key discrete logarithm the attacker knows.

### Finding Description
`ThresholdKeys::read` at crypto/dkg/src/lib.rs:574-632 reads:
- `t`, `n`, `i` as raw `u16`s,
- an interpolation variant tag (0 = Constant with `n` scalars, 1 = Lagrange),
- a raw `secret_share` scalar via `C::read_F`,
- `n` verification shares via `<C as Ciphersuite>::read_G` — which does NOT reject the identity point (crypto/ciphersuite/src/lib.rs:91-101), unlike `Curve::read_G` used elsewhere.

It then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs:347-391), which only checks:
- `verification_shares.len() == n`,
- participant indexes `<= n`,
- Constant interpolation only when `t == n`.

It then computes `group_key = Σ_{i∈1..=t} verification_shares[i] * interpolation_factor(i)` (lines 376-378). It never verifies that:
1. `verification_shares[i]` are non-identity,
2. `secret_share` is consistent with `verification_shares[params.i()]` (i.e., `G * secret_share == verification_shares[i]`),
3. the verification shares form any valid committed polynomial at all.

Contrast with `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-100), which only enforces canonical encoding, and with the PedPoP DKG path (crypto/dkg/pedpop/src/lib.rs:484-499) where shares are verified against committed coefficients before being folded into `self.secret` — a validation entirely absent in the deserialize path.

### Impact Explanation
An attacker who can cause a node to load a crafted serialized `ThresholdKeys` blob (restore/upgrade/import path, or any channel feeding `ThresholdKeys::read`) obtains `ThresholdKeys` whose `group_key()` is a point with a discrete logarithm known only to the attacker. Any funds, outputs, or signing operations attributed to that group key are controlled by the attacker, while the victim believes they hold a share of it. Equally, mismatched `secret_share`/`verification_shares` pairs pass silently, so FROST signing under these keys will produce partial signatures inconsistent with the stored verification shares — corrupting the threshold protocol while appearing structurally valid. This matches "key share recovery"/"funds reported received that are not spendable" (here, spendable by the attacker, not the victim) and the general class of attacker-uploaded structured content being trusted without validation.

### Likelihood Explanation
Reachability depends on whether any in-scope component exposes `ThresholdKeys::read` to untrusted bytes (e.g., key backup/restore, peer-supplied key material, or coordinator key handoff). The scan rules explicitly classify bytes fed to `ThresholdKeys::read` as an untrusted input surface. The exploit requires only crafting the correct serialization — no cryptographic hardness is involved since every field is attacker-chosen and no proof of consistency is demanded. Impact is bounded by the attacker's ability to substitute the key file, so it is rated High rather than Critical absent an unauthenticated remote write path.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391), add:
1. Reject identity verification shares (`verification_shares.values().any(|s| s.is_identity())` → error), matching `Curve::read_G` semantics.
2. Verify the holder's consistency: `C::generator() * *secret_share == verification_shares[&params.i()]`.
3. For `Interpolation::Constant`, verify the secret share equals the constant-interpolated value over the provided scalars; for Lagrange, at minimum check `group_key` is non-identity.
4. Consider an authenticating layer (MAC/signature over the serialization) so `ThresholdKeys::read` cannot ingest third-party-substituted key material.

### Proof of Concept
```rust
// Attacker chooses x and builds a fully self-consistent, attacker-known threshold key.
use zeroize::Zeroizing;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};

// For a Secp256k1-flavored ciphersuite C:
let x = /* attacker-chosen nonzero scalar */;

let mut buf = vec![];
// C::ID length prefix + ID bytes (copied from a legitimate serialization header)
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
// t = 1, n = 1, i = 1
buf.extend(1u16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
// interpolation = Lagrange (tag 1) — avoids the Constant t==n coefficient check anyway
buf.push(1);
// secret_share = x
buf.extend(x.to_repr().as_ref());
// verification_shares[1] = G * x  (identity would also be accepted)
buf.extend((C::generator() * x).to_bytes().as_ref());

// ThresholdKeys::read succeeds; group_key() == G * x — dlog known to attacker.
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();
assert_eq!(keys.group_key(), C::generator() * x);
// Even stronger: set secret_share = 0 while verification_shares[1] = G*x.
// No check fails: internal inconsistency is silently accepted, corrupting
// every subsequent FROST partial signature computed from these keys.
```

Citations for the root cause: `ThresholdKeys::read` at crypto/dkg/src/lib.rs:574-632 (unvalidated field-by-field read), `ThresholdKeys::new` at crypto/dkg/src/lib.rs:347-391 (no share/consistency/identity checks; `group_key` summed from raw shares), non-identity-rejecting `Ciphersuite::read_G` at crypto/ciphersuite/src/lib.rs:91-101, contrasted with identity-rejecting `Curve::read_G` at crypto/frost/src/curve/mod.rs:125-131.

Uncertainty note: I could not exhaustively confirm whether any in-scope crate wires `ThresholdKeys::read` directly to a remote/untrusted byte source rather than local trusted storage; if it is strictly local trusted storage, this degrades to defense-in-depth rather than a live vulnerability. No stronger candidate (e.g., identity-commitment abuse in PedPoP — blocked because the PoK over `commitments[0]` requires dlog knowledge, crypto/dkg/pedpop/src/lib.rs:323-329) survived verification within available iterations.