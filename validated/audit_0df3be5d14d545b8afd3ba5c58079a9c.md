### Title
Empty aggregate Schnorr signature verifies successfully - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts a serialized aggregate containing zero nonce points, while `SchnorrAggregate::verify` only requires the nonce count to equal the number of supplied `(public_key, challenge)` pairs. When both lists are empty and `s = 0`, verification reduces to checking that the identity point equals the identity point and returns `true`.

### Finding Description
`SchnorrAggregate::read` trusts the attacker-controlled four-byte `Rs` length and permits it to be zero, then reads a canonical scalar `s`. [1](#0-0)  During verification, the only explicit validity check is `Rs.len() == keys_and_challenges.len()`. [2](#0-1)  With both inputs empty, no per-signature statements or aggregation weights are generated, and the verifier evaluates only `(-s)G`; choosing `s = 0` therefore passes.

This is inconsistent with `SchnorrAggregator::complete`, which refuses to produce an aggregate when no signatures were aggregated. [3](#0-2) 

### Impact Explanation
An unprivileged party can submit a serialized aggregate signature containing no constituent signatures and have `verify` accept it whenever the verifier permits an empty `keys_and_challenges` list. In a protocol that treats the boolean result as approval for a caller-supplied signature batch, this permits a forged aggregate signature over an empty authorization set—the Serai analogue of an empty authorization value bypassing a security decision.

### Likelihood Explanation
The serialized proof is entirely public-input controlled and requires only four zero length bytes followed by the canonical encoding of scalar zero. Exploitability depends on whether the calling protocol allows the verification statement list to be empty; APIs with a fixed nonempty set are not affected.

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`, or require `keys_and_challenges` to be nonempty before verification. This should mirror the existing `SchnorrAggregator::complete` behavior, which already returns `None` for an empty aggregation.

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs
let mut encoded = vec![0, 0, 0, 0]; // Rs length = 0
encoded.extend(C::F::ZERO.to_repr().as_ref()); // s = 0

let aggregate =
  SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(b"protocol-domain", &[]));
```

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

**File:** crypto/schnorr/src/aggregate.rs (L174-178)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```
