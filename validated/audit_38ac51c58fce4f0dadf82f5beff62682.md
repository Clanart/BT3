### Title
Empty wire-encoded aggregate Schnorr signature verifies successfully - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero nonces, and `SchnorrAggregate::verify` reduces that encoding to the equation `0 * G = 0`, returning `true`. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` parses an attacker-controlled `u32` count followed by that many group elements and a scalar, without rejecting a count of zero. [1](#0-0)  During verification, the code only checks that the encoded `Rs` length equals `keys_and_challenges.len()`, so an empty encoding is paired with an empty expected-signer list. [3](#0-2)  The verifier then emits no nonce or public-key statements and adds only `-s * G`; setting `s = 0` makes the multiexp equal the identity and verification succeeds. [4](#0-3)  This is inconsistent with `SchnorrAggregator::complete`, which refuses to produce an aggregate when no signatures were supplied. [5](#0-4) 

### Impact Explanation
An unauthenticated party can submit a five-or-more-byte wire encoding that deserializes to a signature object and passes `SchnorrAggregate::verify` without proving possession of any private key. [1](#0-0) [4](#0-3)  Any caller that treats this verifier result as authorization for an empty signer set accepts a forged aggregate signature. [2](#0-1) 

### Likelihood Explanation
The forged encoding is deterministic and does not require hash grinding, secret knowledge, malformed scalar encodings, or control over honest signers. [1](#0-0)  Exploitation requires the surrounding protocol to verify an aggregate against an empty `keys_and_challenges` list, which is a degenerate policy but is not rejected by this API. [2](#0-1) 

### Recommendation
Reject `Rs.is_empty()` in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`, matching the producer-side invariant enforced by `SchnorrAggregator::complete`. [1](#0-0) [5](#0-4)  Verification should fail before constructing the multiexp when no signature statements are present. [2](#0-1) 

### Proof of Concept
```rust
use ff::{Field, PrimeField};
use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;
use ciphersuite::Ciphersuite;

// Aggregate encoding: u32 nonce count = 0, followed by scalar s = 0.
let mut encoded = 0u32.to_le_bytes().to_vec();
encoded.extend_from_slice(
  <Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref()
);

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

// The verifier builds only `-0 * G`, which is the identity.
assert!(aggregate.verify(b"example-domain", &[]));
```

The encoding supplies no public-key or nonce statements, while `verify` nevertheless reaches `multiexp_vartime(&pairs).is_identity()` and returns `true` for `s = 0`. [4](#0-3)

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
