### Title
Empty aggregate signature bypasses Schnorr verification - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when supplied with an empty `keys_and_challenges` list and `s = 0`. This lets a caller-controlled empty aggregate satisfy verification without proving knowledge of any private key.

### Finding Description
`SchnorrAggregate::read` deserializes a caller-controlled `u32` count and permits that count to be zero, then reads `s` without requiring it to be nonzero. [1](#0-0) 

`verify` only requires `self.Rs.len() == keys_and_challenges.len()`. If both are empty, the loop queues no weighted `(R, key)` statements and leaves only `(-s, generator)` in the verification equation. [2](#0-1) 

When `s == 0`, `(-0) * generator` is the identity, so verification succeeds. The producer-side API correctly refuses to construct an empty aggregate, but the verifier does not enforce the same invariant for deserialized or otherwise supplied aggregates. [3](#0-2) 

### Impact Explanation
An unprivileged caller can submit bytes representing an empty `Rs` vector and scalar zero, together with an empty key/challenge list, and obtain a successful signature verification without a private key or valid underlying signature. This is a forged signature accepted by the public `verify` API.

### Likelihood Explanation
Exploitation requires the calling protocol to accept an empty aggregate and evaluate it against an empty expected-signer list. `read` makes the empty aggregate directly reachable from untrusted bytes, while `verify` contains no minimum-count check to reject it.

### Recommendation
Require every aggregate signature to contain at least one signature in `SchnorrAggregate::verify`:

```rust
if self.Rs.is_empty() || self.Rs.len() != keys_and_challenges.len() {
  return false;
}
```

Also reject a zero encoded count in `SchnorrAggregate::read` so malformed empty aggregates fail during deserialization.

### Proof of Concept
```rust
let encoded = [
  0, 0, 0, 0, // Rs length = 0
  // 32-byte canonical encoding of scalar zero for the selected ciphersuite
];

let sig = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(sig.verify(b"domain", &[]));
```

The equality check succeeds because both lengths are zero; no weighted statements are queued, and `s = 0` makes the sole `(-s)G` term the identity.

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
