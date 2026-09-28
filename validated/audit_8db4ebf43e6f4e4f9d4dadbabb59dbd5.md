### Title

Unbounded aggregate-signature length enables remote memory and CPU exhaustion - (`crypto/schnorr/src/aggregate.rs`)

### Summary

`SchnorrAggregate::read` accepts an attacker-controlled `u32` signature count and deserializes that many group elements without an upper bound. Each decoded point is retained in `Rs`, allowing a hostile serialized aggregate to grow memory consumption until exhaustion. Subsequent verification additionally performs work proportional to the supplied count.

### Finding Description

The deserializer reads a four-byte length and directly loops over `u32::from_le_bytes(len)`, appending every decoded point to `Rs`. It does not impose a protocol maximum, estimate the expected byte length, or reject an excessive count before allocation. [1](#0-0) 

The impact continues into `verify`, which allocates `2 * keys_and_challenges.len() + 1` scalar-point pairs and computes one aggregation weight for each entry before running multiexponentiation. [2](#0-1) 

### Impact Explanation

An unprivileged party able to submit a serialized aggregate signature can provide a very large count and a stream of valid encoded points. The node will continue allocating vector capacity and decoding group elements, potentially exhausting memory or monopolizing CPU. If verification is reached, the oversized aggregate also triggers a large transcript calculation and multiexponentiation. [3](#0-2) 

This is a Medium-severity denial-of-service issue: it can affect availability, but does not itself forge a signature or disclose key material.

### Likelihood Explanation

`SchnorrAggregate::read` is public and operates directly on untrusted bytes. Any deployment that accepts aggregate signatures before enforcing an external message-size or signature-count limit can reach the vulnerable loop. Exploitation only requires repeatedly supplying valid point encodings; no validator privileges, secret data, malformed curve point, or protocol violation is needed. [1](#0-0) 

### Recommendation

Impose an explicit maximum aggregate-signature count before parsing. At minimum, enforce the smaller of a protocol-defined maximum and a bound derived from the remaining input length or enclosing message limit. Verification should likewise reject `keys_and_challenges` above that maximum before allocating the `pairs` vector.

### Proof of Concept

Conceptual Rust PoC using a reader that repeatedly supplies one valid point encoding:

```rust
use std::io::{self, Read};

struct RepeatingPoint {
  point: Vec<u8>,
  offset: usize,
}

impl Read for RepeatingPoint {
  fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
    for byte in out {
      *byte = self.point[self.offset];
      self.offset = (self.offset + 1) % self.point.len();
    }
    Ok(out.len())
  }
}

// Prefix the stream with u32::MAX little-endian, then repeat a valid
// compressed group-element encoding forever.
let mut reader = prefixed_count_then(RepeatingPoint {
  point: valid_compressed_point_encoding,
  offset: 0,
});

// Grows Rs until memory is exhausted.
let _ = SchnorrAggregate::<Ristretto>::read(&mut reader);
```

The vulnerable behavior is the unbounded loop over the serialized count and the corresponding append to `Rs`. [4](#0-3)

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
