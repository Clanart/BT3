### Title
Empty aggregate signatures are accepted as valid - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero nonces whenever `keys_and_challenges` is also empty and `s == 0`, even though `SchnorrAggregator::complete` deliberately refuses to produce an aggregate when no signatures were supplied. This creates a deserialization/verification inconsistency: an attacker can submit canonical bytes for an empty aggregate and have `verify` return `true` for an empty authorization set.

### Finding Description
`SchnorrAggregate::read` reads a `u32` nonce count, then reads that many group elements and a scalar. A count of zero is accepted and produces `Rs = []`, followed by an attacker-controlled `s`. [1](#0-0) 

`verify` checks only that `self.Rs.len() == keys_and_challenges.len()`. With both lengths equal to zero, no per-signature statements are queued. It then queues only `(-self.s, C::generator())`; when `s == 0`, this is the identity point and `multiexp_vartime(...).is_identity()` succeeds. [2](#0-1) 

This contradicts the aggregation API’s valid-state invariant: `SchnorrAggregator::complete` returns `None` if no signatures were aggregated, so an empty aggregate is not a producible valid signature. [3](#0-2) 

### Impact Explanation
Any caller that accepts a serialized aggregate and verifies it against an attacker-influenced or otherwise empty `keys_and_challenges` set can accept a forged aggregate signature. The forged object requires no secret keys, nonce knowledge, or valid constituent signatures.

This is the Serai analogue of the reported “zero-result” state issue: a boundary object below the intended minimum cardinality is accepted, and later verification treats the resulting zero equation as successful rather than rejecting it.

### Likelihood Explanation
Exploitation requires a verifier path to invoke `SchnorrAggregate::verify` with an empty `keys_and_challenges` list. If the protocol always supplies a fixed non-empty signer set, the issue is unreachable. If the signer/challenge list is derived from attacker-controlled transaction or proof metadata, however, an attacker can select the empty set and provide the nine-word serialized forgery described below.

### Recommendation
Reject empty aggregates at both deserialization and verification boundaries:

```rust
// crypto/schnorr/src/aggregate.rs
if self.Rs.is_empty() || self.Rs.len() != keys_and_challenges.len() {
  return false;
}
```

`SchnorrAggregate::read` should also reject a zero nonce count, matching `SchnorrAggregator::complete`’s refusal to emit an empty aggregate.

### Proof of Concept

```rust
// For any C: Ciphersuite, such as Ristretto:
let encoded = [
  0, 0, 0, 0,                   // Rs length = 0
  /* C::F::ZERO.to_repr() */,   // s = 0
];

let sig = SchnorrAggregate::<C>::read(&mut encoded.as_slice())?;
assert!(sig.verify(dst, &[]));
```

The four-byte count creates `Rs = []`, while canonical zero encodes `s = 0`. Verification succeeds because the empty key list matches `Rs`, no challenge-weighted statements are added, and `0 * G` is identity. [1](#0-0) [2](#0-1)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-87)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
```

**File:** crypto/schnorr/src/aggregate.rs (L127-145)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/schnorr/src/aggregate.rs (L174-185)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
    Some(aggregate)
```
