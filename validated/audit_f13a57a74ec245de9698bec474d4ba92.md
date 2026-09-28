### Title
Schnorr signature verification accepts identity public keys, enabling universal signature forgery - (File: crypto/schnorr/src/lib.rs)

### Summary
`SchnorrSignature::verify` does not reject the identity point as a public key or nonce commitment. Because the verifier equation adds `challenge * public_key`, an identity public key makes that term vanish, allowing any pair satisfying `sG = R` to verify for every challenge and message context. [1](#0-0) 

### Finding Description
`Ciphersuite::read_G` validates only that a group encoding is canonical; it does not reject the identity point. [2](#0-1)  `SchnorrSignature::read` accepts that same permissive decoding for `R`, while `verify` accepts a caller-supplied `public_key` without checking either it or `R` for identity. [3](#0-2)  Verification then evaluates `R + challenge * public_key - sG`, so when `public_key` is identity, the challenge-dependent term is always zero. [4](#0-3) 

### Impact Explanation
An unprivileged party can forge a signature accepted under the identity public key for any challenge and therefore any message whose transcript resolves to that key. If an application accepts attacker-controlled public keys or decodes an identity key with `read_G`, the attacker controls a signing authority without possessing a corresponding secret. The same boundary violation also permits degenerate signatures using an identity `R`, most trivially `(R = identity, s = 0)` under the identity key. [4](#0-3) 

### Likelihood Explanation
The identity encoding is canonical and passes `read_G`, so malformed input is not needed—only a verifier that treats an attacker-selected identity key as a key within the valid public-key domain. [5](#0-4)  The exploit is deterministic, requires no discrete-log knowledge, and works independently of the challenge value. The impact is limited to contexts that allow an identity public key, which keeps this below a universal break of correctly keyed deployments. [6](#0-5) 

### Recommendation
Reject identity points for both `R` and `public_key` in `SchnorrSignature::verify`, or provide a stricter signature-specific point decoder mirroring `Curve::read_G`'s identity rejection. [7](#0-6)  `SchnorrSignature::read` should use the stricter check for `R`, and callers should use a public-key decoder that guarantees non-identity before invoking `verify`. [3](#0-2) 

### Proof of Concept
For any `C: Ciphersuite`, choose:

```rust
let public_key = C::G::identity();
let sig = SchnorrSignature::<C> {
    R: C::generator(),
    s: C::F::ONE,
};

let challenge = C::F::from(12345u64);
assert!(sig.verify(public_key, challenge));
```

The verifier computes `generator + challenge * identity - generator`, which equals identity for every `challenge`, so the forged signature succeeds deterministically. [4](#0-3)

### Citations

**File:** crypto/schnorr/src/lib.rs (L49-58)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }

  /// Write a SchnorrSignature to something implementing Read.
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.R.to_bytes().as_ref())?;
    writer.write_all(self.s.to_repr().as_ref())
```

**File:** crypto/schnorr/src/lib.rs (L86-110)
```rust
  /// Return the series of pairs whose products sum to zero for a valid signature.
  /// This is intended to be used with a multiexp.
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
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L85-100)
```rust
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

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
