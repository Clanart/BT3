### Title
Empty aggregate signature verifies as valid - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when called with an empty `keys_and_challenges` list. Because `SchnorrAggregate::read` permits a zero-length `Rs` vector and `verify` only checks length equality before evaluating the final `-sG` term, the serialized bytes `00 00 00 00 || 0x00 * 32` verify whenever `s == 0`. This allows an unauthenticated party to forge a cryptographically “valid” aggregate signature for an empty statement set.

### Finding Description
`SchnorrAggregate::read` reads a 32-bit count and then deserializes exactly that many group elements, without rejecting a count of zero. [1](#0-0) 

`SchnorrAggregate::verify` rejects only a length mismatch between `self.Rs` and `keys_and_challenges`; it has no explicit non-empty check. [2](#0-1) 

For an empty aggregate and an empty `keys_and_challenges`, the loop queues no per-signature statements, leaving `pairs` with only `(-self.s, C::generator())`. [3](#0-2) 

If the attacker sets `s` to the canonical zero scalar, that final statement is the identity and `multiexp_vartime(&pairs).is_identity()` evaluates true. [4](#0-3) 

This is inconsistent with the aggregation API’s own construction behavior: `SchnorrAggregator::complete` returns `None` when no signatures were aggregated rather than producing an empty aggregate. [5](#0-4) 

### Impact Explanation
Any protocol path that feeds attacker-controlled bytes to `SchnorrAggregate::read` and then calls `verify` can accept a forged aggregate signature for an empty verification statement. The signature does not prove possession of any private key or authorization from any signer.

This is analogous to the reflected-input bug class: an externally supplied encoding crosses a missing validation boundary and reaches a privileged verification result. The concrete outcome is a forged signature accepted by `verify`, not merely malformed input or denial of service.

### Likelihood Explanation
The attack requires the verifier to evaluate an aggregate against an empty `keys_and_challenges` set. That can occur when a request selects no signatures, all signatures are filtered out, or an attacker causes an empty batch to reach verification. The crafted encoding is only four zero length bytes plus the canonical zero scalar representation, and no secret, malformed curve point, timing dependency, or protocol privilege is needed.

If production callers always reject empty batches before calling `verify`, the reachable impact is reduced; the primitive itself nevertheless incorrectly reports the forged aggregate as valid.

### Recommendation
Reject empty aggregates at both boundaries:

```rust
// crypto/schnorr/src/aggregate.rs
if self.Rs.is_empty() {
  return false;
}
```

in `SchnorrAggregate::verify`, and preferably also reject `len == 0` in `SchnorrAggregate::read` so the invalid object cannot be constructed from untrusted bytes. Add a regression test asserting that `00 00 00 00 || zero-scalar` fails verification for an empty statement list.

### Proof of Concept

```rust
use std::io;
use dalek_ff_group::Ristretto;
use schnorr::SchnorrAggregate;

fn forged_empty_aggregate() -> io::Result<()> {
  // u32 little-endian Rs length = 0, followed by the canonical zero scalar.
  let mut encoded = Vec::new();
  encoded.extend_from_slice(&0u32.to_le_bytes());
  encoded.extend_from_slice(&[0u8; 32]);

  let aggregate =
    SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice())?;

  // For an empty statement list, the only multiexp statement is 0 * G.
  assert!(aggregate.verify(b"application DST", &[]));

  Ok(())
}
```

`verify` builds no per-signature statements for the empty list and evaluates only `-0G == identity`, so the assertion succeeds. [6](#0-5)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
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
