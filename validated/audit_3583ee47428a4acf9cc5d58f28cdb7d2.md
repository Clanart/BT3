### Title
Identity public key and nonce are accepted, enabling trivial Schnorr signature forgery - ([File: crypto/schnorr/src/lib.rs](https://github.com/blackvul/serai--018/blob/main/crypto/schnorr/src/lib.rs))

### Summary
`SchnorrSignature::read` accepts the identity point for `R`, and `verify` accepts the identity point as `public_key`. A signature with `R = identity` and `s = 0` satisfies the verification equation for any challenge when the public key is also identity.

### Finding Description
`SchnorrSignature::read` deserializes `R` through `C::read_G` and `s` through `C::read_F`, without rejecting an identity nonce commitment [1](#0-0) . The generic `Ciphersuite::read_G` only checks that the point is valid and canonically encoded; it does not reject identity [2](#0-1) . Verification checks the equation `R + challenge * public_key - s * G == identity`, which is satisfied by `R = identity`, `public_key = identity`, and `s = 0` for every challenge [3](#0-2) .

### Impact Explanation
An unprivileged party can submit untrusted bytes that deserialize to an identity public key and an identity-`R`, zero-`s` signature. Any caller which treats successful `verify` as proof of authorization accepts a signature over a message without any private key. This is a forged signature accepted by the verifier, not merely a malformed-input rejection issue.

### Likelihood Explanation
The attack is deterministic and requires no interaction beyond causing the verifier to process the attacker-controlled identity public key and signature bytes. Reachability depends on an integrator accepting serialized public keys from untrusted input; the vulnerable primitives themselves are public deserialization and verification APIs.

### Recommendation
Reject the identity public key and identity `R` during signature verification, and preferably reject identity `R` in `SchnorrSignature::read`. Applications that separately deserialize public keys should also reject identity keys before calling `verify`.

### Proof of Concept
For any ciphersuite `C` implementing `Ciphersuite`, construct:

```text
public_key = C::G::identity()
signature.R = C::G::identity()
signature.s = C::F::ZERO
challenge = arbitrary C::F
```

`batch_statements` evaluates:

```text
1 * identity + challenge * identity - 0 * G = identity
```

so `verify(public_key, challenge)` returns `true` for every challenge [4](#0-3) . The canonical encodings of the identity point and zero scalar pass `read_G` and `read_F` because those functions enforce canonical encodings but not nonzero values [5](#0-4) .

### Citations

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-109)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
```

**File:** crypto/ciphersuite/src/lib.rs (L74-100)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }

  /// Read a canonical point from something implementing std::io::Read.
  ///
  /// The provided implementation is safe so long as `GroupEncoding::to_bytes` always returns a
  /// canonical serialization.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
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
