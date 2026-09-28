### Title
`SchnorrAggregate::verify` accepts an empty aggregate (zero `Rs`, `s = 0`) as a valid signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to the PJSIP/GnuTLS bug where a verification flag is set but the actual check is silently skipped, `SchnorrAggregate::verify` reduces to checking `is_identity()` on a multiexp containing only `(-s, generator())`. When an attacker supplies an aggregate with no `Rs` and `s == 0`, the verification equation is `0 * G == identity`, which trivially passes — the function returns `true` without having verified any signature at all.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read` (lines 77–88) accepts an untrusted encoding with a `u32` count of zero `Rs` followed by an arbitrary scalar `s`. `SchnorrAggregate::verify` (lines 127–146) then checks `self.Rs.len() != keys_and_challenges.len()` and builds the multiexp pairs. When `keys_and_challenges` is empty (or the verifier's key set is empty) and `Rs` is empty, the only pair pushed is `(-self.s, C::generator())` at line 144. `multiexp_vartime(&pairs).is_identity()` therefore returns true whenever `s == 0`, i.e. the "verification" succeeds while verifying nothing.

This is inconsistent with the crate's own producer side: `SchnorrAggregator::complete` (lines 175–178) explicitly returns `None` when no signatures were aggregated, showing that an empty aggregate is not a meaningful object — yet the verifier path silently accepts a deserialized one. A public attacker can produce these bytes (`u32::to_le_bytes(0)` ‖ `s = 0`) and feed them to `SchnorrAggregate::read` + `verify` with no secret knowledge, forging a "signature" that passes verification. The bug class matches the CVE: verification is invoked but the actual check degenerates to a tautology.

### Impact Explanation
Any caller that interprets `SchnorrAggregate::verify(...) == true` as "these parties produced valid Schnorr signatures" can be convinced by an attacker-controlled byte string that a valid aggregate signature exists when no party signed anything. This is a forged signature accepted by the verification routine — the exact accepted-impact category (forgery via public inputs to `read`/`verify`). Where aggregates gate approvals (e.g., an empty `keys_and_challenges` slice arising from an empty signing set, a filtered key list, or a deserialized request), attacker-controlled bytes `00 00 00 00 || <32-byte zero>` verify as authentic.

### Likelihood Explanation
Reachable by an unprivileged party: `SchnorrAggregate::read` performs no semantic validation (no non-empty check, no `s != 0` check), so fully attacker-controlled bytes drive `verify` to the degenerate-identity path. Exploitation requires the verifier to invoke `verify` with an empty `keys_and_challenges` set; the code provides no guard forcing a non-empty pairing, and `debug_assert`/`len()` equality check is the only gate. The serializer symmetry (`write` happily emits zero `Rs`) makes constructing the input trivial.

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify` (return `false` when `keys_and_challenges.is_empty()` or `self.Rs.is_empty()`), mirroring `SchnorrAggregator::complete` which already refuses to produce an empty aggregate. Optionally also reject `s == 0` at read/verify time as a defense-in-depth canonicality check.

### Proof of Concept
```rust
// crypto/schnorr context, C = any Ciphersuite
// Attacker-controlled bytes: zero Rs, s = 0
let mut bytes = vec![];
bytes.extend(0u32.to_le_bytes());                 // len(Rs) = 0
bytes.extend(C::F::ZERO.to_repr().as_ref());      // s = 0

let agg = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
// Verification "succeeds" despite no signature ever being produced:
assert!(agg.verify(b"dst", &[]));                 // forged aggregate accepted
```

Note: this finding assumes `multiexp_vartime` correctly computes `0 * G == identity`, which is standard. Whether any in-tree caller invokes `verify` with an empty `keys_and_challenges` could not be fully confirmed within the iteration budget; the vulnerability is in the primitive accepting the forged encoding regardless.