### Title
Empty serialized aggregate bypasses Schnorr aggregate verification - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate encoding containing zero nonces and a zero scalar, while `SchnorrAggregate::verify` accepts that object when called with an empty signer/challenge list. [1](#0-0) [2](#0-1) 

### Finding Description
Deserialization reads the encoded nonce count and permits a count of zero, then reads `s` normally. [3](#0-2)  Verification only requires `Rs.len() == keys_and_challenges.len()`; when both lengths are zero, the sole remaining statement is `-sG`, which is the identity for `s = 0`. [2](#0-1)  This is inconsistent with the producing API, which explicitly refuses to create an aggregate from zero signatures. [4](#0-3) 

### Impact Explanation
An attacker can submit the raw encoding `0u32 || 0 scalar` and obtain `verify(dst, &[]) == true` without possessing any signing key. [2](#0-1)  Any caller that treats this verifier as proof that an aggregate signature exists for a supplied signer set, but relies on a separate caller-controlled or empty signer list, can accept a forged aggregate authorization result. [1](#0-0) 

### Likelihood Explanation
The malformed object is only 36 bytes for a 32-byte scalar field and requires no private state, collision, timing condition, or cooperative signer. [3](#0-2)  Exploitation requires reaching a verification path that does not independently reject an empty signer set; the verifier itself does not enforce that invariant despite the aggregation API doing so. [2](#0-1) [5](#0-4) 

### Recommendation
Reject empty `keys_and_challenges`/`Rs` in `SchnorrAggregate::verify`, matching `SchnorrAggregator::complete`; preferably also reject an encoded nonce count of zero during `SchnorrAggregate::read` unless an empty aggregate has a deliberately defined representation. [2](#0-1) [4](#0-3) 

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs
// Encoding: little-endian nonce count = 0, followed by canonical scalar zero.
let mut encoded = Vec::from(0u32.to_le_bytes());
encoded.extend([0u8; 32]);

let aggregate = SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.verify(b"example-domain-separator", &[]));
```

The verification equation reduces to `(-0)G == identity`, so the forged empty aggregate verifies. [6](#0-5)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-88)
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

**File:** crypto/schnorr/src/aggregate.rs (L174-185)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
    Some(aggregate)
```
