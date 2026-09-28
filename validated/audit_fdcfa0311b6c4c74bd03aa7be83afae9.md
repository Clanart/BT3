### Title
Empty aggregate signature verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts a serialized aggregate containing zero nonces and a zero scalar, even though `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`SchnorrAggregate::read` trusts the public length prefix and permits it to be zero, then reads the scalar directly. [1](#0-0)  During verification, an empty `keys_and_challenges` slice passes the length check when `Rs` is empty, produces no weighted signature terms, and leaves only `(-s) * G` in the multiexponentiation. [4](#0-3)  For `s == 0`, that expression is the identity, so verification returns true. [2](#0-1)  This contradicts the producer boundary, where `SchnorrAggregator::complete` returns `None` for zero signatures rather than constructing a valid aggregate. [3](#0-2) 

### Impact Explanation
An unprivileged party can provide public bytes that decode to a valid aggregate signature for an empty signer set, causing an incorrect verifier result and a forged aggregate signature under the API’s verification semantics. [1](#0-0) [4](#0-3) 

### Likelihood Explanation
The required input is only a little-endian zero length followed by the canonical representation of scalar zero; no secret, malformed encoding, or trusted party is required. [1](#0-0) 

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`, matching the producer-side invariant in `SchnorrAggregator::complete`. [1](#0-0) [4](#0-3) [3](#0-2) 

### Proof of Concept
```rust
let mut bytes = 0_u32.to_le_bytes().to_vec();
bytes.extend(C::F::ZERO.to_repr().as_ref());

let aggregate = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
assert!(aggregate.verify(dst, &[]));
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
