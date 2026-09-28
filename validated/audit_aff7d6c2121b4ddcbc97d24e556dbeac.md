### Title
Untrusted `SchnorrAggregate` length can cause memory exhaustion - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` accepts an unbounded attacker-controlled `u32` element count and appends one decoded group element per requested entry, without a protocol-level maximum. A peer able to supply bytes for aggregate verification can therefore force unbounded allocation and deplete process memory. [1](#0-0) 

### Finding Description
The first four serialized bytes are interpreted as a little-endian `u32`, then `C::read_G(reader)` is invoked in a loop for every requested `R`. Each successful decode is retained in `Rs`, so the retained allocation grows to `O(len)` group elements. [2](#0-1) 

Unlike a fixed-size `SchnorrSignature::read`, which consumes only one point and one scalar, `SchnorrAggregate::read` exposes a remote-size-controlled vector. [3](#0-2) 

The result is subsequently verified against caller-provided keys and challenges; verification itself requires `Rs.len()` to equal `keys_and_challenges.len()`, but that check happens only after the potentially enormous vector has already been decoded and retained. [4](#0-3) 

### Impact Explanation
A service that accepts serialized aggregate signatures from untrusted callers can be made to allocate memory proportional to attacker-controlled input. At the maximum declared count, the parser attempts to store up to 4,294,967,295 group elements before reading the final scalar, requiring well over 100 GiB of live `Vec<C::G>` storage and likely exhausting available memory before parsing completes. [5](#0-4) 

This is a remote loss-of-availability condition: no malformed cryptographic values are required, only enough valid canonical group encodings to grow the retained vector to the resource limit. Canonical point decoding is performed by `C::read_G`. [6](#0-5) 

### Likelihood Explanation
The attacker controls the leading count and all subsequent encodings. Any deserialization endpoint that reads aggregate signatures before authentication or rate limiting is exposed; the API does not require proof of key ownership, message binding, or a threshold parameter before storage is accumulated. [1](#0-0) 

The issue is bounded in practice by how much serialized input the caller permits, but the deserializer provides no intrinsic aggregate-size ceiling. Verification cannot prevent the issue because length validation occurs after parsing. [7](#0-6) 

### Recommendation
Bound the declared aggregate length before allocation or parsing. Ideally, callers should pass an expected maximum/signature count derived from protocol state, and `read` should reject `len > expected_count` before reading any points. If the API must remain standalone, impose a conservative protocol maximum, chunk parsing only as needed, and avoid retaining `Rs` unless the complete encoding is valid. Additionally, reserve `Vec` capacity only after validating a bounded count. [8](#0-7) 

### Proof of Concept
```rust
use std::io::{Cursor, Read, Repeat, repeat};

use schnorr::aggregate::SchnorrAggregate;
use ciphersuite::Ciphersuite;

fn attacker_controlled_reader<C: Ciphersuite>() -> impl Read
where
  C::G: group::GroupEncoding,
{
  // Declares the maximum possible number of aggregate entries.
  let len = u32::MAX.to_le_bytes();

  // Represents a network-backed stream of repeated canonical encodings.
  // In a concrete target, replace repeat(0x00) with a valid `C::G` encoding
  // repeated by the attacker-controlled reader.
  Cursor::new(len).chain(repeat(0x00))
}

// Calling this on an unbounded reader repeatedly decodes into `Rs`.
// With valid point bytes, memory grows until the process exhausts memory.
let mut reader = attacker_controlled_reader::<MyCiphersuite>();
let _ = SchnorrAggregate::<MyCiphersuite>::read(&mut reader);
```

The controlling behavior is the direct `for _ in 0 .. u32::from_le_bytes(len)` loop pushing each decoded point into `Rs`. [2](#0-1)

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

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
