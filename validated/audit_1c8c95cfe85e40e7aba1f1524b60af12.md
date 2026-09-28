### Title
Empty serialized Schnorr aggregate is accepted as valid - ([File: crypto/schnorr/src/aggregate.rs])

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero `R` values and `s = 0`, while `SchnorrAggregate::verify` treats the resulting empty verification statement as valid. [1](#0-0) [2](#0-1) 

### Finding Description
The deserializer reads an attacker-controlled count, permits that count to be zero, and then accepts the canonical zero scalar for `s`. [1](#0-0) 

Verification only requires the serialized `Rs` count to equal the number of supplied key/challenge pairs; when both are empty, it constructs a batch containing only `(-s) * G`. [3](#0-2) 

With `s = 0`, that sole term is the identity, so the verification equation succeeds without verifying any signature. [4](#0-3) 

The generating API does not produce this state: `SchnorrAggregator::complete` explicitly returns `None` when no signatures were aggregated. [5](#0-4) 

### Impact Explanation
An attacker can submit a 36-byte serialized aggregate that verifies successfully for an empty `keys_and_challenges` list, despite no party having produced a Schnorr signature. [1](#0-0) 

This is a forged aggregate-signature acceptance condition reachable entirely through public bytes passed to `SchnorrAggregate::read` and then `verify`. [1](#0-0) [2](#0-1) 

### Likelihood Explanation
The malformed object has a fixed, trivial encoding: a little-endian count of zero followed by the canonical zero scalar encoding. [1](#0-0) [6](#0-5) 

Exploitation requires a caller to invoke aggregate verification for an empty expected-signature set, but no secret access, signing participation, malformed scalar encoding, or invalid point encoding is needed. [2](#0-1) 

### Recommendation
Reject empty `Rs` values in `SchnorrAggregate::read`, reject an empty `keys_and_challenges` list in `SchnorrAggregate::verify`, or enforce both checks before evaluating the multiexp. [1](#0-0) [2](#0-1) 

A regression test should assert that the serialized zero-count aggregate is rejected and cannot be constructed through `SchnorrAggregator::complete`. [5](#0-4) 

### Proof of Concept
For any supported `Ciphersuite`, a 36-byte input consisting of `u32::MAX`-compatible zero count bytes followed by the scalar-zero representation is accepted by `read`; this example uses the crate’s existing Ed25519 test ciphersuite. [7](#0-6) 

```rust
use dalek_ff_group::Ed25519;
use schnorr::aggregate::SchnorrAggregate;

const DST: &[u8] = b"Schnorr Aggregator Test";

let mut encoded = Vec::new();
encoded.extend_from_slice(&0u32.to_le_bytes()); // zero Rs
encoded.extend_from_slice(&[0u8; 32]);        // canonical s = 0 for Ed25519

let aggregate =
  SchnorrAggregate::<Ed25519>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(DST, &[]));
```

The assertion succeeds because verification creates no `R` or public-key terms and evaluates only the zero `-s * G` term. [4](#0-3)

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

**File:** crypto/schnorr/src/aggregate.rs (L174-179)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

```

**File:** crypto/ciphersuite/src/lib.rs (L74-82)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
```

**File:** crypto/schnorr/src/tests/mod.rs (L6-15)
```rust
use dalek_ff_group::Ed25519;
use ciphersuite::{
  group::{ff::Field, Group},
  Ciphersuite,
};
use multiexp::BatchVerifier;

use crate::SchnorrSignature;
#[cfg(feature = "aggregate")]
use crate::aggregate::{SchnorrAggregator, SchnorrAggregate};
```
