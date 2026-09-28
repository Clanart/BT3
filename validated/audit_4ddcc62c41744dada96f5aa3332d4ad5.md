### Title
Empty aggregate Schnorr signatures verify successfully - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::verify` accepts the serialized aggregate containing zero nonces and a zero `s` scalar when the verifier supplies an empty `keys_and_challenges` list. The verification equation then reduces to `0 == 0`, producing a successful result without any signer having produced a signature. [1](#0-0) 

### Finding Description
`SchnorrAggregate::read` accepts a nonce-vector length of zero and reads `s` directly afterward. [2](#0-1)  During verification, the only structural check is that the number of supplied nonces equals the number of supplied public-key/challenge pairs. [3](#0-2)  With both empty, the loop adds no weighted nonce or public-key terms, and only `(-0)G` is evaluated. [4](#0-3)  The resulting identity point causes verification to return `true`. [5](#0-4) 

### Impact Explanation
A remote party who can submit an aggregate signature encoding can forge an accepted aggregate signature for an empty signer statement. This is a false-positive verification result analogous to a signature check reporting success despite the underlying signature set being invalid. Any caller that treats `SchnorrAggregate::verify` as evidence that at least one listed signer authorized a message may accept a completely unsigned aggregate if an empty signer list can reach the API.

### Likelihood Explanation
The bytes required are fully public and trivially constructible: a four-byte zero length followed by a canonical zero scalar. Exploitation requires the surrounding protocol to permit an empty signer list or otherwise associate security with an aggregate containing no signatures. Because production callers are expected to enforce a quorum separately, the practical severity is medium rather than high.

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify` before evaluating the multiexp. Preferably also reject them in `SchnorrAggregate::read`, since an aggregate signature containing no signatures has no valid verification meaning. `SchnorrAggregator::complete` already refuses to produce an empty aggregate, so making verification reject the same degenerate form would align deserialization and verification behavior. [6](#0-5) 

### Proof of Concept
```rust
use ciphersuite::{Ciphersuite, group::ff::PrimeField};
use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

// Four-byte little-endian nonce count of zero, followed by scalar zero.
let mut encoded = vec![0, 0, 0, 0];
encoded.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());

let aggregate =
  SchnorrAggregate::<Ristretto>::read::<&[u8]>(&mut encoded.as_ref()).unwrap();

// This returns true despite the aggregate containing no signer, nonce,
// public key, challenge, or signature contribution.
assert!(aggregate.verify(b"example-dst", &[]));
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-88)
```rust
  /// Read a SchnorrAggregate from something implementing Read.
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

**File:** crypto/schnorr/src/aggregate.rs (L174-184)
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
```
