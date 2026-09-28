### Title
`SchnorrAggregate::verify` accepts an empty aggregate (0 `Rs`, `s = 0`) as valid, reporting verification success when no signature was actually verified - ([File: crypto/schnorr/src/aggregate.rs])

### Summary
Analogous to GHSA-7mqr-2v3q-v2wm (a revocation handler reporting success despite the underlying operation not being performed), `SchnorrAggregate::verify` in `crypto/schnorr` reports `true` for a degenerate aggregate containing zero signatures. Because the multiexp over the statement list reduces to `(-s)·G` alone, supplying `Rs = []` and `s = 0` makes `multiexp_vartime(&pairs)` trivially the identity, so verification returns `true` without a single signature having been checked. An unprivileged party can reach this via `SchnorrAggregate::read` with untrusted bytes (`u32 len = 0` followed by a zero scalar).

### Finding Description
`SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) reads a `u32` count of `R` points and a scalar `s`; a count of `0` is accepted. `SchnorrAggregate::verify` (lines 127-146) checks `self.Rs.len() == keys_and_challenges.len()` — satisfied when both are empty — then builds `pairs` containing only `(-self.s, C::generator())`. With `s = 0`, the multiexp result is the identity and `verify` returns `true` (lines 138-145):

```rust
// crypto/schnorr/src/aggregate.rs
if self.Rs.len() != keys_and_challenges.len() {
  return false;
}
...
pairs.push((-self.s, C::generator()));
multiexp_vartime(&pairs).is_identity().into()
```

Unlike `BatchVerifier::queue` (crypto/multiexp/src/batch.rs:39-89), which guards the edge case by giving the first queued statement weight `ONE` and rejecting zero random weights, `SchnorrAggregate::verify` has no minimum-statement requirement at all. The verification equation `Σ z·R_i + Σ z·c_i·A_i - s·G = 0` degenerates to `-s·G = 0`, solvable by anyone with `s = 0`.

This mirrors the fosite flaw's structure: an operation whose exceptional/degenerate condition (nothing to verify) is silently treated as success instead of an error.

### Impact Explanation
`SchnorrAggregate` implements half-aggregation per eprint 2021/350; its purpose is to prove that *each* of N signers produced a valid Schnorr signature. A verifier using it over a signer set that can legitimately be empty — e.g., a threshold/committee set reduced to zero members after removals, or a caller that iterates a dynamically computed `keys_and_challenges` list — would accept a fully attacker-forged "aggregate signature" (`len=0`, `s=0`) as proof that the (empty) set signed. This is an incorrect verifier formula at the boundary: it certifies authenticity of nothing while returning `true`, satisfying the "forged signature/proof accepted" acceptance criterion. Severity is bounded by requiring a caller whose signer set can be empty, hence Medium rather than High.

### Likelihood Explanation
Reachability is direct: `SchnorrAggregate::read` is a public untrusted-bytes entry point and imposes no lower bound on `Rs.len()`. Exploitation requires only that some consuming protocol call `verify` with a matching empty `keys_and_challenges` slice — plausible wherever the signer set is derived from external state (validator sets, participant lists) that may transiently be empty. No key material, collusion, or privileged position is needed; the "signature" is 36 bytes of mostly zeros.

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify` (e.g., `if self.Rs.is_empty() || self.Rs.len() != keys_and_challenges.len() { return false; }`) and/or reject `len == 0` in `SchnorrAggregate::read`. This parallels the fix pattern in the upstream advisory: the exceptional case must produce an error, not a success result. A doc comment stating the non-emptiness precondition would also help integrators who bypass `verify` with their own multiexp.

### Proof of Concept
```rust
// crypto/schnorr — conceptual PoC against SchnorrAggregate
// Attacker-crafted bytes: u32 count = 0, then s = 0 (canonical zero scalar)
let mut bytes = 0u32.to_le_bytes().to_vec();
bytes.extend(Scalar::ZERO.to_repr()); // C::F for the ciphersuite

let agg = SchnorrAggregate::<Secp256k1>::read(&mut bytes.as_slice()).unwrap();
// keys_and_challenges is empty whenever the derived signer set is empty
assert!(agg.verify(b"any dst", &[])); // returns true; nothing was verified
```

Caveat: end-to-end impact depends on an in-scope consumer calling `verify` with an empty `keys_and_challenges` list; I did not find such a caller in the indexed scope, so exploitability rests on the verifier accepting the degenerate input at its boundary rather than on a specific deployed call site.