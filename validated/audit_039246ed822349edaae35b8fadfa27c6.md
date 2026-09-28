### Title
Empty aggregate signature verification bypasses signer authentication - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate signature containing zero nonces when the caller supplies zero public-key/challenge pairs. A serialized aggregate with `Rs.len() == 0` and `s == 0` therefore verifies successfully without any signer, private key, nonce, or valid individual signature. [1](#0-0) 

### Finding Description
`SchnorrAggregate::verify` only checks that `self.Rs.len() == keys_and_challenges.len()`. It does not require at least one signature or key. [2](#0-1) 

When both slices are empty, the verifier creates a statement list containing only `-sG`. [3](#0-2)  If the attacker supplies `s = 0`, that expression is the identity point and `multiexp_vartime` reports success. [4](#0-3) 

The deserializer permits this malformed aggregate because it accepts a zero `Rs` count and then reads only the scalar `s`. [5](#0-4)  Although `SchnorrAggregator::complete` refuses to produce an empty aggregate locally, `SchnorrAggregate::read` creates an externally supplied object that bypasses that guard. [6](#0-5) 

### Impact Explanation
An unprivileged attacker can forge an aggregate Schnorr signature that verifies against an empty signer set. The forged object contains no public nonce and no scalar response from any signer, yet `verify` returns `true`. This is analogous to accepting a request as authenticated because no authentication material was present: the verifier treats the empty proof as valid instead of requiring at least one authenticated statement.

Any protocol path that derives authorization from `SchnorrAggregate::verify` without separately enforcing a nonempty signer set can accept an attacker-controlled empty aggregate as approval.

### Likelihood Explanation
The attack requires only public bytes passed to `SchnorrAggregate::read` and a call to `verify` with an empty `keys_and_challenges` slice. No private key, participant interaction, malicious validator, leaked secret, invalid curve point, or malformed encoding is required. The serialized payload is a zero count followed by the canonical zero scalar.

The direct cryptographic impact is limited to callers that accept an empty signer set as meaningful or accidentally reach `verify` with an empty expected-key list. The primitive itself nevertheless performs a successful verification of a forged aggregate signature rather than rejecting it.

### Recommendation
Reject empty aggregates in both deserialization-independent verification and construction paths:

```rust
if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
  return false;
}
```

Optionally reject zero-length aggregates during `SchnorrAggregate::read` as defense in depth. Add a regression test which deserializes a zero-count aggregate with `s = 0` and asserts that `verify` returns `false` for an empty key/challenge list.

### Proof of Concept
```rust
use ciphersuite::{group::ff::PrimeField, Ciphersuite};
use frost::curve::Secp256k1;
use schnorr::aggregate::SchnorrAggregate;

fn empty_aggregate_verifies() {
  // Encoding format:
  //   u32 little-endian Rs length || canonical scalar s
  let mut encoded = Vec::new();
  encoded.extend(0u32.to_le_bytes());
  encoded.extend(<Secp256k1 as Ciphersuite>::F::ZERO.to_repr().as_ref());

  let forged =
    SchnorrAggregate::<Secp256k1>::read(&mut encoded.as_slice()).unwrap();

  // No public keys, challenges, nonces, or signatures are supplied.
  assert!(forged.verify(b"example aggregate DST", &[]));
}
```

The forged statement succeeds because `pairs` reduces to `(-0) * G`, which is the identity. [3](#0-2)

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

**File:** crypto/schnorr/src/aggregate.rs (L174-179)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

```
