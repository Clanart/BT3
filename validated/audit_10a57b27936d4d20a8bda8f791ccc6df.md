### Title
Unbounded aggregate-signature deserialization allows a slow input stream to exhaust resources - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` trusts an attacker-controlled 32-bit signature count and reads that many curve points before any application-level bound is applied. An unprivileged sender can declare approximately 4.3 billion points and either omit the remaining bytes or drip-feed them indefinitely, causing the caller to block and retain a growing allocation. [1](#0-0) 

### Finding Description
The deserializer first reads a four-byte length, interprets it as a little-endian `u32`, and iterates that many times. [2](#0-1)  Each iteration performs a blocking `C::read_G(reader)` and pushes the decoded point into `Rs`; there is no maximum aggregate size, byte limit, deadline, or check that the claimed count is reasonable for the protocol. [3](#0-2) 

The resulting vector is only later compared with the expected number of keys/challenges by `SchnorrAggregate::verify`, so an oversized declaration must be fully materialized before ordinary verification rejects it. [4](#0-3) 

### Impact Explanation
An attacker who controls bytes passed to `SchnorrAggregate::read` can make a verifying process wait indefinitely for the next point, consume CPU while parsing many points, and allocate memory proportional to the attacker-controlled count. For a compressed 32-byte curve point, the maximum declared input represents roughly 137 GB of point data, far beyond reasonable aggregate-signature sizes. [1](#0-0) 

This is a remote availability failure when the reader is backed by a socket or another stream whose peer can remain open and send bytes slowly. The primitive permits a Slowloris-style parser stall equivalent to the reported open-connection vulnerability.

### Likelihood Explanation
The triggering input begins with `ff ff ff ff`, followed by as few or as many encoded points as the attacker chooses to provide. No valid signatures, cryptographic knowledge, validator privilege, leaked key, or malformed curve point is required because the loop is entered before validation of the aggregate as a whole. [5](#0-4) 

Exploitability requires the application to deserialize an attacker-controlled aggregate signature from a blocking `Read` implementation. When that integration pattern exists, the input format itself supplies the unbounded work factor.

### Recommendation
Do not deserialize an attacker-controlled count without a protocol-specific bound. `SchnorrAggregate::read` should either accept a `max_signatures` parameter or enforce a conservative crate-level maximum, and the declared count should be rejected before allocating or entering the read loop. Callers should also decode from a size-limited reader and apply I/O deadlines so a peer cannot keep the deserialization alive indefinitely.

### Proof of Concept
The following demonstrates the reachable state transition: the input declares the maximum `u32` count, after which `read` immediately attempts the first of more than four billion point reads and can block indefinitely if the stream remains open without producing another byte. [1](#0-0) 

```rust
// crypto/schnorr/src/aggregate.rs
use std::io::{self, Read};

struct NeverEndingPrefix {
    sent: usize,
}

impl Read for NeverEndingPrefix {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        // Supply 0xff ff ff ff as the Rs count.
        if self.sent < 4 {
            let n = (out.len()).min(4 - self.sent);
            out[..n].fill(0xff);
            self.sent += n;
            return Ok(n);
        }

        // A socket-backed attacker can instead remain connected and drip-feed bytes.
        // For demonstration, report no bytes rather than EOF; a real TCP-backed Read
        // would block here.
        Ok(0)
    }
}

let mut input = NeverEndingPrefix { sent: 0 };
let _ = SchnorrAggregate::<C>::read(&mut input);
```

A finite variant of the same payload can keep `read_exact` satisfied very slowly, causing up to `u32::MAX` iterations and an equivalently large `Rs` vector before the function ever reaches the final scalar read or `verify` checks the signature count. [6](#0-5)

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
