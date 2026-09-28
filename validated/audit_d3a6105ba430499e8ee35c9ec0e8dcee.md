### Title
Aggregate Schnorr verification accepts an empty signer set - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` only checks that the serialized nonce count equals the supplied key/challenge count, but does not require that count to be non-zero. [1](#0-0)  An attacker can therefore submit an aggregate signature containing no `R` values and `s = 0`, which verifies successfully against an empty `keys_and_challenges` list. [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts a zero length prefix and then reads only the scalar `s`. [3](#0-2)  During verification, no challenge is appended and no `(z, R)` or `(z * challenge, key)` pair is queued when the list is empty; the sole remaining pair is `(-s, G)`. [4](#0-3)  With `s = 0`, that term is the identity, so `multiexp_vartime` returns the identity and verification succeeds. [2](#0-1) 

### Impact Explanation
A caller that exposes aggregate verification over attacker-controlled signature batches can accept a forged aggregate authorization without any constituent signer or valid signature. [5](#0-4)  This is analogous to bypassing a privileged burn by supplying the forbidden zero destination: the empty signer set is the unrestricted sentinel case omitted by validation. [1](#0-0) 

### Likelihood Explanation
The condition is directly reachable through public serialization bytes: a four-byte zero length followed by a canonical zero scalar produces the malicious object. [3](#0-2)  Exploitation requires the surrounding verifier to permit an empty aggregate input rather than independently rejecting it. [1](#0-0) 

### Recommendation
Reject empty `keys_and_challenges` in `SchnorrAggregate::verify`, and preferably reject serializations whose nonce count is zero at the `SchnorrAggregate::read` boundary. [3](#0-2)  If empty aggregates are intentionally representable, return `false` before constructing the multi-scalar multiplication. [6](#0-5) 

### Proof of Concept
For any ciphersuite whose canonical scalar representation is `N` bytes, the serialized proof is:

```rust
let mut forged = Vec::new();
forged.extend(0u32.to_le_bytes()); // zero Rs
forged.extend([0u8; N]);           // canonical s = 0

let signature = SchnorrAggregate::<C>::read(&mut forged.as_slice()).unwrap();
assert!(signature.verify(dst, &[]));
```

The zero count passes the length comparison, no signature terms are queued, and `-s * G` is the identity because `s` is zero. [6](#0-5)

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

**File:** crypto/schnorr/src/aggregate.rs (L118-145)
```rust
  /// Perform signature verification.
  ///
  /// Challenges must be properly crafted, which means being binding to the public key, nonce, and
  /// any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  ///
  /// The DST used here must prevent a collision with whatever hash function produced the
  /// challenges.
  #[must_use]
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
