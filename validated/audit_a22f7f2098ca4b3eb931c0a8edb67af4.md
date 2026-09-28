### Title
Zero-value Schnorr signature (R = identity, s = 0) verifies under an identity public key — (File: crypto/schnorr/src/lib.rs)

### Summary
The YOLO report describes entries contributing zero value still being accepted as valid tickets that can "win" because the verification logic treats a zero-contribution entry identically to a real one. The direct analog in Serai is `SchnorrSignature::verify` / `batch_statements`, which accepts the all-zero signature `(R = identity, s = 0)` for any public key equal to the identity, under *any* challenge — the zero-value "signature" passes with no secret knowledge. The same zero-acceptance propagates into `BatchVerifier`-queued statements and `SchnorrAggregate` half-aggregation, where an identity key included in the key list contributes a trivially satisfiable statement.

### Finding Description
`SchnorrSignature::verify` computes `multiexp_vartime` over `batch_statements`: `R + c·A − s·G == 0` (`crypto/schnorr/src/lib.rs:88-110`). When the public key `A` is the identity point and the attacker supplies `R = identity, s = 0`, the statement is `identity + c·identity − 0·G = identity`, which passes for **every** challenge `c` — i.e., for every message. There is no `is_identity` check on `R`, on `s`, or on the public key anywhere in `verify`, `batch_verify`, or `read`.

Two compounding details:

- `SchnorrSignature::read` (`crypto/schnorr/src/lib.rs:51-53`) only enforces canonical encoding via `C::read_G` / `C::read_F`; the identity point and zero scalar are canonical encodings, so the forged signature deserializes cleanly from untrusted bytes.
- `BatchVerifier::queue` (`crypto/multiexp/src/batch.rs:46-88`) gives the *first* queued statement a fixed weight of `ONE`. An attacker who can queue an `(identity, 0)` signature statement as the first batch item gets a free pass on that item with weight 1; subsequent items get random non-zero weights. This mirrors the report's "same currentEntryIndex → later entry selected" mechanic only weakly, but the zero-statement acceptance is exact.

For key reachability: `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:376-378`) computes `group_key` as a weighted sum of `verification_shares` with no identity check on the result or on individual shares; `Ciphersuite::read_G`/`read_F` do not reject identity/zero by contract. Any caller that verifies Schnorr signatures (or aggregates them via `crypto/schnorr/src/aggregate.rs`, where `SchnorrAggregate::verify` takes a caller-supplied key list) against an attacker-influenced public key admitting the identity — e.g., a key derived so the aggregate/effective key is identity — gets a universal forgery: `(identity, 0)` signs every message.

### Impact Explanation
Forged Schnorr signature valid under any challenge (any message) for an identity public key. In any context where an attacker can cause a verifying party to use an identity public key — registration paths that deserialize keys via `read_G` without an identity rejection, or aggregate/threshold constructions whose combined key can be forced to identity — the attacker authenticates as that key at zero cost, directly paralleling "win the pot with a 0-value entry."

### Likelihood Explanation
The cryptographic flaw is unconditional: the equation `0 + c·0 − 0 = 0` holds for all `c`. Exploitability hinges on an identity public key being accepted upstream. `read_G` accepts canonical identity encodings and neither `verify`, `ThresholdKeys::new`, nor the aggregate verifier reject identity keys, so any path registering an attacker-supplied or attacker-influenced identity key (e.g., key cancellation in multikey aggregation making the effective key identity) is exploitable. Where higher layers enforce non-identity, non-cancellable keys, the exposure is latent rather than live.

### Recommendation
In `SchnorrSignature::verify` and `batch_statements`/`batch_verify`, reject `R.is_identity()` and `s.is_zero()` (and document/reject identity `public_key`). In `SchnorrAggregate::verify`, reject identity public keys and identity `R` components per signature. In `ThresholdKeys::new`, reject an identity `group_key` and identity `verification_shares` so a zero-valued key can never reach verification.

### Proof of Concept
```rust
// crypto/schnorr — for ANY challenge c and A = identity:
// batch_statements yields [ (1, id), (c, id), (-0, G) ]
// multiexp sum = id + c·id - 0·G = id  → verify() returns true
let sig = SchnorrSignature::<Ed25519> { R: EdwardsPoint::identity(), s: Scalar::ZERO };
assert!(sig.verify(EdwardsPoint::identity(), any_challenge)); // forged, no secret

// BatchVerifier: first queued statement gets weight ONE, so the
// zero statement passes batch verification with weight 1
// (crypto/multiexp/src/batch.rs:46-48)
```

*Caveat:* I could not fully trace every upstream key-registration path to confirm whether identity public keys are reachable in a live protocol path (e.g., whether MuSig/DKG callers elsewhere reject identity keys); the verifier-level acceptance itself is proven by the formula in `batch_statements`, but concrete end-to-end exploitability depends on a caller admitting an identity or attacker-cancellable public key.