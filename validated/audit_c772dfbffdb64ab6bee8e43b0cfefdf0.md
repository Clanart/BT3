### Title
`SchnorrAggregate::verify` accepts an empty aggregate signature (`Rs = []`, `s = 0`) as valid for any message — (File: crypto/schnorr/src/aggregate.rs)

### Summary
The Node advisory's bug class is an enforcement check applied on all comparable paths but omitted on one reachable path. Serai's Schnorr half-aggregation exhibits exactly this asymmetry: `SchnorrAggregator::complete()` explicitly refuses to produce an aggregate over zero signatures (`if self.sigs.is_empty() { return None; }`, `crypto/schnorr/src/aggregate.rs:175-178`), yet the verification side, `SchnorrAggregate::verify` (`crypto/schnorr/src/aggregate.rs:127-146`), performs no equivalent non-emptiness check. An attacker-controlled byte string encoding `len = 0` followed by `s = 0` deserializes via `SchnorrAggregate::read` (`crypto/schnorr/src/aggregate.rs:77-88`) and verifies successfully against an empty `keys_and_challenges` slice, for any `dst` and any message.

### Finding Description
In `verify`:

```rust
if self.Rs.len() != keys_and_challenges.len() { return false; }
// digest over challenges — none appended for the empty case
let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() { ... }
pairs.push((-self.s, C::generator()));
multiexp_vartime(&pairs).is_identity().into()
```

When `keys_and_challenges` is empty and `Rs` is empty, the loop contributes nothing and `pairs` reduces to the single statement `(-s)·G`. With `s = 0` (a canonically-encoded zero scalar, accepted by `C::read_F`), `multiexp_vartime` returns the identity via `Algorithm::Single` (`crypto/multiexp/src/lib.rs:185,197`), so `verify` returns `true`.

This is not a hypothetical edge: `verify` is the sole aggregate-verification path, and the aggregator already encodes the invariant "an aggregate must contain at least one signature" on the production side only. The verification path lacks the corresponding guard — precisely the "missing check on one of several comparable paths" shape of the report.

The reachable consumer is `Validators::verify_aggregate` in `coordinator/tributary/src/tendermint/mod.rs:201-229`: it reads the attacker-supplied `AggregateSignature` bytes, checks `signers.len() == aggregate.Rs().len()`, computes per-signer challenges, and returns `aggregate.verify(...)`. If invoked with an empty `signers` set (e.g., a commit/step carrying no validator signatures, before any higher layer independently rejects an empty signer list), a forged 4-byte `len=0 || 32-byte zero s` blob is accepted as a valid aggregate over any `msg`.

### Impact Explanation
Forged signature acceptance: a byte string that no signer produced verifies as a valid aggregate Schnorr signature for arbitrary messages under arbitrary DSTs. Any downstream logic treating "aggregate verified over signer set S" as authorization is bypassed when S can be empty at the point of verification. Severity: Medium — impact is a signature forgery, but exploitation requires a caller that forwards an empty signer set rather than rejecting it first (consensus layers typically require ≥2/3-weight signatures, which bounds practical reachability).

### Likelihood Explanation
Low-to-moderate. `SchnorrAggregator::complete` can never produce the empty aggregate, so only a maliciously hand-crafted encoding triggers it. Whether `verify_aggregate` in the Tendermint layer is ever called with `signers.is_empty()` depends on tendermint-core's commit handling; the crypto crate itself unconditionally accepts the case, so every present and future caller inherits the requirement to pre-validate non-emptiness — an undocumented assumption.

### Recommendation
In `SchnorrAggregate::verify`, add `if self.Rs.is_empty() { return false; }` (equivalently, reject empty `keys_and_challenges`), mirroring `SchnorrAggregator::complete`'s empty-set rejection so both sides enforce the same invariant. Optionally also reject identity/`s == 0` encodings in `read` for defense in depth, consistent with `Signed::read`'s identity rejection in `coordinator/tributary/src/transaction.rs:62-69`.

### Proof of Concept
```rust
// Attacker bytes: u32 len = 0, then a canonical zero scalar
let mut bytes = 0u32.to_le_bytes().to_vec();
bytes.extend([0u8; 32]); // s = 0, canonical for Ristretto/k256 scalar fields

let agg = SchnorrAggregate::<Ristretto>::read(&mut bytes.as_slice()).unwrap();
// Any DST, empty signer set:
assert!(agg.verify(b"any dst", &[])); // returns true — forgery accepted
```
Correspondingly, in `Validators::verify_aggregate`, `signers = &[]` plus this encoding yields `true` for any `msg` and any `genesis`, since the challenge loop produces no entries and `verify` short-circuits to checking `(-0)·G == identity`.