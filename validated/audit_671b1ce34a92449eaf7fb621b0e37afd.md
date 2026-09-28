### Title
Forged empty aggregate Schnorr signatures accepted by `SchnorrAggregate::verify` - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts a serialized aggregate containing zero nonces and a zero scalar when the expected `keys_and_challenges` list is also empty. `SchnorrAggregator::complete` explicitly refuses to produce an aggregate for an empty signature set, but the deserializer and verifier do not enforce that same semantic validity condition. This is analogous to accepting an empty order with insufficient collateral: the verifier checks the algebraic equation while omitting the non-emptiness invariant. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts a `u32` nonce count of `0`, followed by one canonical scalar for `s`. [3](#0-2)  `SchnorrAggregate::verify` first checks only that `self.Rs.len() == keys_and_challenges.len()`, so an empty aggregate is considered length-compatible with an empty verification list. [4](#0-3) 

For an empty list, the verifier queues no per-signature statements and evaluates only `(-s) * G`. [5](#0-4)  When the attacker supplies `s = 0`, that expression is the identity point, so verification returns `true`. [6](#0-5) 

This object cannot be produced through the intended aggregation API because `SchnorrAggregator::complete` returns `None` when no signatures were aggregated. [7](#0-6)  The public deserialization path therefore admits a semantically invalid signature object that the constructor API explicitly rejects. [3](#0-2) [2](#0-1) 

### Impact Explanation
An unprivileged party can submit public bytes representing an empty aggregate and have them accepted as a valid aggregate signature for an empty statement. In a caller that interprets `verify(...) == true` as authorization for a batch whose contents are supplied elsewhere, this creates a forged-signature acceptance path rather than merely rejecting an empty batch as invalid. The accepted object is also outside the range of signatures the legitimate aggregator can emit. [1](#0-0) [2](#0-1) 

### Likelihood Explanation
The attack requires a caller to verify an aggregate over an empty key/challenge collection. The exploit itself is deterministic once that API usage exists: serialization imposes no minimum length, and a zero scalar is canonical, so no secret, privileged access, malformed encoding, or malicious validator is required. [3](#0-2) [8](#0-7) 

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`. At minimum, `verify` should return `false` when `keys_and_challenges.is_empty()`; preferably, `read` should also reject a zero nonce count so deserialized values maintain the same invariant enforced by `SchnorrAggregator::complete`. [3](#0-2) [4](#0-3) [2](#0-1) 

### Proof of Concept
For any supported `Ciphersuite`, construct the wire encoding:

```rust
let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes()); // zero R points
encoded.extend(C::F::ZERO.to_repr().as_ref()); // s = 0

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.verify(b"example-dst", &[]));
```

The length check succeeds because both collections are empty, the multiexp reduces to `(-0) * G`, and `multiexp_vartime` returns the identity point. The same empty aggregate cannot be generated honestly because `SchnorrAggregator::complete` returns `None` for `self.sigs.is_empty()`. [3](#0-2) [1](#0-0) [2](#0-1)

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

**File:** crypto/ciphersuite/src/lib.rs (L71-83)
```rust
  /// Read a canonical scalar from something implementing std::io::Read.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```
