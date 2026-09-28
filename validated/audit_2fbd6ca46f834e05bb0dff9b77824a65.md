### Title
Unbounded aggregate-signature count enables remote memory and CPU denial of service - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` trusts a 32-bit count embedded in attacker-controlled bytes and iterates that many times without enforcing a protocol-level maximum. [1](#0-0) 

### Finding Description
The parser reads four bytes, interprets them as a little-endian `u32`, and appends one decoded group element to `Rs` for each declared entry. [1](#0-0)  No bound ties this count to the expected validator/signing-set size or rejects implausible values before parsing. [2](#0-1) 

Each attacker-supplied group element is decoded through `C::read_G`, stored in a growing `Vec`, and only after all declared elements are processed does the parser read the scalar `s`. [3](#0-2)  The resulting `Rs` length is subsequently used during verification to allocate and populate a multiexponentiation statement list with two entries per supplied key. [4](#0-3) 

### Impact Explanation
An unprivileged party able to submit aggregate-signature bytes can force repeated point decompression, dynamic vector growth, and memory retention proportional to the declared signature count. [3](#0-2)  A large encoded aggregate can consume substantial CPU and memory before the later `s` field is even reached, degrading or terminating the process depending on allocator and runtime limits. [5](#0-4) 

If the attacker also supplies enough signatures for verification, `verify` allocates `2 * n + 1` scalar-point pairs and performs a multiexponentiation over the attacker-sized input. [6](#0-5) 

### Likelihood Explanation
The only required primitive is supplying bytes to `SchnorrAggregate::read`; no secret access, validator compromise, malformed curve implementation, or protocol violation beyond an oversized aggregate is needed. [7](#0-6)  A short input declaring an excessive count fails at EOF, but a padded input containing many encodable group elements successfully drives allocation and parsing work until the declared count is exhausted. [3](#0-2) 

### Recommendation
Pass the expected signer count into `SchnorrAggregate::read`, reject encoded lengths other than that count before reading any points, and additionally enforce a hard maximum aggregate size. [1](#0-0)  At minimum, validate `u32::from_le_bytes(len)` against an application/protocol bound before entering the loop. [2](#0-1) 

### Proof of Concept
```rust
use std::io::Read;
use schnorr::aggregate::SchnorrAggregate;
use dalek_ff_group::Ristretto;
use ciphersuite::{group::GroupEncoding, Ciphersuite};

fn oversized_aggregate(count: u32) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&count.to_le_bytes());

    // Repeat the canonical encoding of the generator `count` times.
    let point = Ristretto::generator().to_bytes();
    for _ in 0 .. count {
        bytes.extend_from_slice(point.as_ref());
    }

    // Append a canonical scalar for `s`.
    bytes.extend_from_slice(
        <Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref()
    );
    bytes
}

let input = oversized_aggregate(10_000_000);
let mut reader = input.as_slice();
let _ = SchnorrAggregate::<Ristretto>::read(&mut reader);
```

The controlled `count` determines how many point decodings and `Vec` insertions occur, with no parser-side maximum. [8](#0-7)  Verification further amplifies the attacker-controlled size into a separate multiexponentiation workload. [6](#0-5)

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
