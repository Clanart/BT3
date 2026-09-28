### Title
Empty aggregate signature bypasses Schnorr authorization - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero nonces, and `SchnorrAggregate::verify` accepts it when the verifier supplies an empty `keys_and_challenges` list. This lets an unauthenticated byte string satisfy signature verification without any signer or private key.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count and permits that count to be zero before reading the aggregate scalar `s`. [1](#0-0)  `verify` only requires the number of supplied nonces to equal the number of key/challenge entries. [2](#0-1)  For two empty lists, no signature statements are generated and the only statement is `-sG`; choosing `s = 0` makes the multiexp identity and returns true. [3](#0-2)  The legitimate aggregation path explicitly refuses to produce an aggregate when no signatures were supplied, demonstrating that an empty aggregate is not intended to be valid. [4](#0-3) 

### Impact Explanation
An attacker can provide the serialized encoding of zero `R` values followed by a canonical zero scalar, and any authorization flow that calls `verify(dst, &[])` will accept it as a valid aggregate signature. This is a forged signature acceptance: no public key owner participates and no private key or nonce knowledge is required. [5](#0-4) 

### Likelihood Explanation
Exploitation requires a caller to invoke `verify` with an empty authorization set, which can occur when signer or challenge lists are derived dynamically from an empty transaction, plan, participant set, or filtered message. Because the deserializer itself creates the invalid state and `verify` does not enforce the non-empty invariant used by `complete`, the condition is directly reachable through untrusted signature bytes once such an empty verification context exists. [1](#0-0) [4](#0-3) 

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`. Prefer enforcing `!Rs.is_empty()` as a type invariant during deserialization or construction, and return `false` or an error if `keys_and_challenges` is empty.

### Proof of Concept
For a ciphersuite `C` whose canonical zero scalar encodes as zero bytes:

```rust
// crypto/schnorr/src/aggregate.rs
let mut encoded = 0u32.to_le_bytes().to_vec();
encoded.extend(C::F::ZERO.to_repr().as_ref());

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.verify(dst, &[]));
```

The four-byte length causes zero `R` values to be read, while the zero scalar makes the final `-sG` statement identity. [1](#0-0) [3](#0-2)

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
