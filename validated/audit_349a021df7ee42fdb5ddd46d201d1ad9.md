### Title
Empty aggregate Schnorr signature bypasses all signature verification - (File: crypto/schnorr/src/aggregate.rs)

### Summary

`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when `s = 0`, even though `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate. [1](#0-0) [2](#0-1) 

### Finding Description

`SchnorrAggregate::read` accepts a serialized aggregate whose nonce count is `0`, followed by a scalar. [3](#0-2)  During verification, an empty `keys_and_challenges` list passes the length check for an aggregate with `Rs.len() == 0`, adds no signature terms, and leaves only the single multiexponentiation term `-s * G`. [1](#0-0)  If `s` is zero, that term is the identity and verification returns `true`. [4](#0-3) 

This creates an inconsistent trust boundary: aggregation treats the empty case as invalid by returning `None`, while deserialization and verification treat a handcrafted empty aggregate as valid. [2](#0-1) 

### Impact Explanation

An unauthenticated party can construct a forged aggregate signature that verifies without any signer, nonce, secret share, or valid challenge relationship. [1](#0-0)  Any protocol that calls `SchnorrAggregate::verify` after receiving attacker-controlled aggregate bytes and an empty signer/challenge set can accept authorization that no participant approved. [3](#0-2) 

### Likelihood Explanation

The attack requires only public bytes: a four-byte zero length and the canonical encoding of scalar zero. [3](#0-2)  Exploitation is deterministic and does not depend on signature malleability, random oracle behavior, or control over any private key. [5](#0-4) 

### Recommendation

Reject empty aggregates in `SchnorrAggregate::read` or at the start of `SchnorrAggregate::verify`, consistent with `SchnorrAggregator::complete` returning `None` for zero signatures. [6](#0-5) [2](#0-1) 

### Proof of Concept

```rust
// crypto/schnorr/src/aggregate.rs context.
let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes()); // Rs.len() == 0
encoded.extend(C::F::ZERO.to_repr().as_ref()); // s == 0

let aggregate =
  SchnorrAggregate::<C>::read::<&[u8]>(&mut encoded.as_slice()).unwrap();

// Accepted even though no signer participated.
assert!(aggregate.verify(DST, &[]));
```

`verify` reaches `pairs.push((-self.s, C::generator()))`; with `s == 0`, the multiexponentiation is the identity and the function returns `true`. [4](#0-3)

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

**File:** crypto/schnorr/src/aggregate.rs (L174-178)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```
