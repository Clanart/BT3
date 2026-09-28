### Title
Missing identity check on reconstructed `group_key`/verification shares in `ThresholdKeys::new` enables forging signatures under a zero group key - (File: crypto/dkg/src/lib.rs)

### Summary
The Telcoin report's bug class is "a value is produced/accepted without checking it isn't the zero value". In Serai, `ThresholdKeys::read` accepts untrusted bytes for all `n` verification shares, and `ThresholdKeys::new` interpolates shares `1..=t` into `group_key` without ever checking that the resulting group key — or any verification share — is non-identity. A crafted `ThresholdKeys` blob whose verification shares sum to the identity produces a key set whose group key is the identity point, under which *any* Schnorr/FROST signature trivially verifies.

### Finding Description
`ThresholdKeys::read` reads `n` verification shares using `<C as Ciphersuite>::read_G`, which checks canonicity/torsion but explicitly does **not** reject the identity point (the identity-rejecting variant is `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`, which is not used here). It then calls `ThresholdKeys::new`, which computes `group_key` as a weighted sum of the first `t` verification shares and stores it unconditionally — there is no `is_identity` check on either the individual shares or the final `group_key` (crypto/dkg/src/lib.rs:376-390).

Downstream, `ThresholdView::group_key()` returns this point, and FROST verification reduces to `s*G == R + c*A` where `A` is the group key. With `A = identity`, the check reduces to `s*G == R`, which any unprivileged party satisfies by choosing arbitrary `s` and setting `R = s*G` — a universal forgery for any message under that key set.

### Impact Explanation
Any consumer that deserializes `ThresholdKeys` from an untrusted source (`ThresholdKeys::read` is explicitly an untrusted-bytes sink) and then verifies FROST/Schnorr signatures against `group_key()` accepts forged signatures for arbitrary messages — e.g., forged `set_keys`/eventuality signatures — because the verification equation degenerates when the public key is the identity.

### Likelihood Explanation
Requires an attacker to supply or corrupt a serialized `ThresholdKeys` blob consumed by a verifier (all-identity verification shares suffice: Lagrange interpolation of identity points is identity, and `interpolation_factor` weights don't matter). No honest-path randomness produces identity shares, so this only triggers via the untrusted read path, matching the "attacker-controlled bytes into `read`" reachability model.

### Recommendation
In `ThresholdKeys::new` (and/or `ThresholdKeys::read`), reject identity verification shares and assert the interpolated `group_key` is non-identity, e.g. `if bool::from(group_key.is_identity()) { Err(DkgError::...) }`, consistent with `Curve::read_G`'s identity rejection.

### Proof of Concept
Serialize a `ThresholdKeys` where every `verification_shares[l] = C::G::identity()` (identity encodings pass `Ciphersuite::read_G`'s canonical check since `identity.to_bytes()` round-trips). `ThresholdKeys::read` succeeds; `group_key()` is identity. For any message `m`, pick scalar `s`, set `R = s*G`, compute the transcript challenge `c` (which will satisfy `s*G = R + c*identity` for the honest `s`), and the forged signature `(R, s)` verifies.