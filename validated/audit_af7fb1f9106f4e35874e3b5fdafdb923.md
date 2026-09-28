### Title
Empty aggregate signature bypasses the producer-side non-empty check - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregator::complete` refuses to produce an aggregate over zero signatures, but `SchnorrAggregate::read` accepts a serialized aggregate with zero `R` values. `SchnorrAggregate::verify` then accepts that aggregate when `s == 0` and the caller supplies an empty `keys_and_challenges` list. This lets an unauthenticated byte string forge a valid empty aggregate proof. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The in-memory construction path establishes a security invariant: `complete` returns `None` when `self.sigs.is_empty()`. The deserialization path does not enforce that invariant because `SchnorrAggregate::read` accepts a length of zero and then reads only the scalar `s`. During verification, an empty `keys_and_challenges` list causes the loop to add no weighted `R` or public-key terms, leaving only `(-self.s) * generator`; when the attacker supplies canonical zero for `s`, the multiexp result is the identity and verification succeeds. [1](#0-0) [4](#0-3) [5](#0-4) 

### Impact Explanation
A caller treating `SchnorrAggregate::verify(dst, &[])` as proof that at least one signature authorized an action can be given a forged proof containing no public keys or signatures. The resulting security impact depends on the caller, but the primitive accepts an explicitly unintended proof state that its producer API refuses to construct. [2](#0-1) [3](#0-2) 

### Likelihood Explanation
The attacker only needs to submit serialized bytes controlled by them to `SchnorrAggregate::read` and cause verification against an empty challenge list. The payload is deterministic and does not require solving a discrete logarithm, predicting randomness, or compromising a participant. [1](#0-0) [4](#0-3) 

### Recommendation
Reject empty aggregates in `SchnorrAggregate::read` and defensively reject empty `keys_and_challenges` in `SchnorrAggregate::verify`. The deserialization invariant should match `SchnorrAggregator::complete`, which refuses empty input. [1](#0-0) [6](#0-5) [5](#0-4) 

### Proof of Concept
For any supported ciphersuite `C`, construct the serialization as a four-byte little-endian count of zero followed by the canonical encoding of scalar zero:

```rust
// crypto/schnorr/src/aggregate.rs

let mut encoded = 0u32.to_le_bytes().to_vec();
encoded.extend_from_slice(C::F::ZERO.to_repr().as_ref());

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.Rs().is_empty());
assert!(aggregate.verify(b"example_dst", &[]));
```

The verification multiexp contains only `(-0) * generator`, which is the identity, so verification returns true despite there being no constituent signatures. [1](#0-0) [7](#0-6)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
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
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L127-146)
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
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L174-179)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

```
