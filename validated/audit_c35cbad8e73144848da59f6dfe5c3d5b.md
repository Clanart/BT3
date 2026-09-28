### Title
Empty aggregate signature verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when `s == 0`, even though `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` permits the encoded `Rs` length to be zero and then reads only the scalar `s`. [3](#0-2) 

During verification, an empty `keys_and_challenges` slice passes the length check when `Rs` is also empty. [4](#0-3) 

The verifier then builds no per-signature terms and pushes only `(-s, generator)` into the multiexponentiation. [5](#0-4) 

For `s == 0`, that sole term is the identity, so verification returns `true` for an aggregate that certifies no public keys, messages, or signatures. [6](#0-5) 

### Impact Explanation
An attacker can forge a serialized aggregate signature that passes `SchnorrAggregate::verify` without any signer participation or private-key knowledge. [3](#0-2) [5](#0-4) 

This creates an ambiguity between “no aggregate exists” and “a valid aggregate exists”: the honest aggregation API returns `None` for zero signatures, but the verifier accepts attacker-supplied bytes representing zero signatures. [2](#0-1) 

Any downstream protocol treating successful aggregate verification as proof that at least one listed signature was verified can be bypassed if it accepts an empty public-key/challenge list. [1](#0-0) 

### Likelihood Explanation
The attack requires only public input: four zero bytes for the `Rs` length followed by a canonical encoding of scalar zero. [3](#0-2) 

No malformed point encoding, secret data, network position, timing behavior, or control over honest signers is required. [7](#0-6) 

Exploitation requires the calling protocol to invoke `verify` with an empty `keys_and_challenges` list, which the current API does not reject. [4](#0-3) 

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify`, preferably before checking or iterating over `keys_and_challenges`. [4](#0-3) 

For example, return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`, matching the producer-side behavior where `SchnorrAggregator::complete` returns `None` for an empty aggregate. [2](#0-1) 

A defense-in-depth check can also reject zero-length `Rs` values in `SchnorrAggregate::read`, although verification should still enforce non-emptiness for independently constructed values. [3](#0-2) 

### Proof of Concept
The following byte construction encodes an aggregate with zero `Rs` values and `s = 0`, then verifies it against an empty statement list:

```rust
use std::io::Cursor;
use frost::curve::Secp256k1;
use schnorr::aggregate::SchnorrAggregate;

let mut encoded = Vec::new();
encoded.extend_from_slice(&0u32.to_le_bytes()); // Rs.len() == 0
encoded.extend_from_slice(&[0u8; 32]);          // canonical secp256k1 scalar zero

let mut reader = Cursor::new(encoded);
let aggregate = SchnorrAggregate::<Secp256k1>::read(&mut reader).unwrap();

assert!(aggregate.verify(b"example dst", &[]));
```

`read` accepts the zero-length vector and scalar encoding, while `verify` evaluates only `(-0) * generator`, which is the identity and therefore returns `true`. [3](#0-2) [5](#0-4)

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
