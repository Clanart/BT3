### Title
Empty aggregate Schnorr signatures are accepted as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero nonces whenever the caller supplies zero `(public_key, challenge)` pairs and `s == 0`. `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate, establishing that an empty signature is not a valid protocol output. The verifier nevertheless lacks a corresponding non-empty check. This is a missing-validation analogue reachable entirely through attacker-controlled serialized bytes passed to `SchnorrAggregate::read` followed by `verify`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`SchnorrAggregate::read` trusts the serialized `Rs` length without requiring it to be nonzero. During verification, it only checks `self.Rs.len() == keys_and_challenges.len()`. If both lengths are zero, no signature statements are queued. The verifier then queues only `(-self.s, generator)`, so `s = 0` causes the multiexp result to be the identity and returns `true`. [1](#0-0) [2](#0-1) 

The aggregation-side API confirms this input is semantically invalid: `SchnorrAggregator::complete` returns `None` when `self.sigs.is_empty()`. Thus deserialization and verification can create an accepted state that the legitimate signer-side API intentionally cannot produce. [4](#0-3) 

### Impact Explanation
An unprivileged party can fabricate an aggregate signature that verifies against an empty signer/challenge set. Any protocol using aggregate verification as an authorization or admission check, where an empty signer list can be reached through attacker-controlled state or edge-case handling, may treat a forged aggregate as valid. This constitutes acceptance of a signature object that the implementation's own aggregation API refuses to create. [2](#0-1) [4](#0-3) 

### Likelihood Explanation
The attack requires only public input bytes: a serialized aggregate with nonce count `0` and canonical scalar `0`, plus an empty signer/challenge list. No secret keys, valid Schnorr signatures, malicious validator assumptions, or cryptographic breaks are needed. Exploitability depends on the caller allowing an empty signer set, but `verify` itself neither rejects that condition nor documents an internal guard. [1](#0-0) [5](#0-4) 

### Recommendation
Reject empty aggregates in both parsing and verification. In `SchnorrAggregate::verify`, return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()` before comparing lengths. Preferably also reject a zero serialized nonce count in `SchnorrAggregate::read`, making the invalid object unrepresentable after deserialization. Keep the existing `SchnorrAggregator::complete` empty-input rejection. [1](#0-0) [2](#0-1) 

### Proof of Concept
Conceptually, for any supported `Ciphersuite`:

```rust
// crypto/schnorr/src/aggregate.rs
let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes());       // zero Rs
encoded.extend(C::F::ZERO.to_repr());     // s = 0

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();

// Accepted even though SchnorrAggregator::complete() refuses empty aggregates.
assert!(aggregate.verify(DST, &[]));
```

The verification equation contains only `(-0) * G`, which is the identity, so `verify` returns `true`. [6](#0-5)

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
