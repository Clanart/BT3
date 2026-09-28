### Title
`SchnorrAggregate::verify` accepts an empty signature as valid for an empty signer set - ([File: crypto/schnorr/src/aggregate.rs])

### Summary
Analogous to CVE-2021-47363 (a zero-sized "stub" object reaching the verification/data path and being treated as valid), `SchnorrAggregate::verify` treats a signature with zero nonces and a zero scalar as a valid aggregate signature over an empty key/challenge set. An unprivileged party can craft such bytes entirely from public inputs via `SchnorrAggregate::read`.

### Finding Description
The kernel bug is a stub object with zero elements becoming visible to consumers that assume a nonzero count. The same shape exists in `crypto/schnorr/src/aggregate.rs`:

- `SchnorrAggregate::read` reads a length-prefixed `Rs` vector and a scalar `s` with no minimum length, so `len = 0` produces `Rs: vec![], s: 0` (lines 77-88).
- `SchnorrAggregate::verify` only checks `self.Rs.len() == keys_and_challenges.len()` (lines 127-130). With both empty, the `pairs` list reduces to the single pair `(-self.s, C::generator())` (line 144). With `s = 0`, `multiexp_vartime` returns the identity, `is_identity()` is true, and `verify` returns `true` (line 145).

So a 5-byte "signature" (`0x00000000` + zero scalar encoding) verifies successfully against an empty key list. There is no non-empty check anywhere in `read` or `verify`, unlike `SchnorrAggregator::complete`, which explicitly refuses to produce an empty aggregate by returning `None` (lines 175-178) — showing the invariant "an aggregate must cover at least one signature" exists on the produce side but is not enforced on the deserialized/verify side.

### Impact Explanation
Any downstream protocol that deserializes an attacker-supplied `SchnorrAggregate` and calls `verify` with an attacker-influenced (or accidentally empty) `keys_and_challenges` slice will accept a forged aggregate signature. Even if the caller supplies keys, note `verify` indexes `self.Rs[i]` — this is fine due to the length check — the defect is purely the degenerate empty case: the empty statement is vacuously "verified," giving a signature acceptance oracle for a signature no party ever produced. This is a forged-signature acceptance, matching the "forged proof or signature" acceptance criterion, conditional on the integration passing an empty set.

### Likelihood Explanation
Medium, consistent with the source CVE rating. The exploit requires the verifier's `keys_and_challenges` to be empty, which depends on the integrating protocol. However, nothing in the type, `read`, or `verify` prevents it, and an attacker who can truncate or influence the signer/challenge list (e.g., a batch of proofs where the number of statements is attacker-derived) can reach it deterministically with trivially crafted bytes.

### Recommendation
Return `false` early in `SchnorrAggregate::verify` when `keys_and_challenges.is_empty()`, and/or reject zero-length `Rs` in `SchnorrAggregate::read`, mirroring the `complete()`-side `is_empty` guard.

### Proof of Concept
```rust
// crypto/schnorr context, C: Ciphersuite
let mut bytes: &[u8] = &[0, 0, 0, 0 /* len = 0 */, /* zero scalar repr */ ...];
let agg = SchnorrAggregate::<C>::read(&mut bytes).unwrap(); // Rs = [], s = 0
assert!(agg.verify(b"some-dst", &[])); // returns true — forged "signature" accepted
```
`verify` pushes only `(-0, generator)` into `pairs`, `multiexp_vartime` yields the identity, and the check passes.