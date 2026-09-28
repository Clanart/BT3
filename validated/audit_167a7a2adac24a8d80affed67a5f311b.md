### Title
Empty aggregate Schnorr signatures are accepted as valid without any signer - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary

`SchnorrAggregate::verify` accepts a serialized aggregate containing zero nonce commitments and a zero `s` scalar when called with an empty `keys_and_challenges` list. This mirrors the missing-account authentication pattern: the verifier reduces an absent signer set to an empty equation and accepts the all-zero proof. [1](#0-0) 

### Finding Description

`SchnorrAggregate::read` accepts a length of zero, producing `Rs: []`, and then reads a scalar which may be zero. [2](#0-1) 

`verify` only requires `Rs.len() == keys_and_challenges.len()`, so both sides can be empty. [3](#0-2) 

For an empty signer set, the verification equation contains only `(-s)G`; setting `s = 0` makes it the identity and verification succeeds. [4](#0-3) 

The signer-side API explicitly refuses to produce an empty aggregate, indicating that verification accepting a手工-constructed empty aggregate is inconsistent and unintended. [5](#0-4) 

### Impact Explanation

An unauthenticated caller can forge an aggregate Schnorr signature for the empty signer set using only public input bytes. If any authorization path delegates aggregate validity to `SchnorrAggregate::verify` and separately reaches an empty signer list, the forged signature can satisfy that signature check without any private key or participant cooperation. [1](#0-0) 

### Likelihood Explanation

The attack requires no timing, randomness, secret material, malformed encoding, or existing signer. The public serialization format directly permits the necessary input, and `read` accepts canonical zero scalars. [2](#0-1) 

### Recommendation

Reject empty aggregate signatures in `SchnorrAggregate::verify`, for example by returning `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`. This should be enforced in the verifier rather than relying on every caller to reject empty signer sets. [1](#0-0) 

### Proof of Concept

For any `Ciphersuite` whose scalar representation is 32 bytes, the following byte string deserializes as an aggregate with zero `Rs` and `s = 0`:

```rust
let mut encoded = Vec::new();
encoded.extend_from_slice(&0u32.to_le_bytes()); // Rs.len() == 0
encoded.extend_from_slice(&[0u8; 32]);          // s == 0

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.verify(b"any-domain-separator", &[]));
```

The assertion succeeds because the equality check passes for two empty lists and the final equation is `-0 * G == identity`. [4](#0-3)

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
