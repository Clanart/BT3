### Title
Identity public keys accepted by `Ciphersuite::read_G` enable universal Schnorr-signature forgery - ([File: crypto/schnorr/src/lib.rs])

### Summary
`Ciphersuite::read_G` accepts the canonical encoding of the identity point, while `SchnorrSignature::verify` only checks the group equation `R + cA - sG = 0` and does not reject an identity public key or nonce. Consequently, an attacker can present the identity point as a public key and construct a signature that verifies for every challenge.

### Finding Description
The generic point parser reads a point encoding and checks only that decoding succeeds and that re-encoding is canonical; it does not reject the identity point. [1](#0-0)  `SchnorrSignature::read` uses that parser for `R`, so an identity nonce is also accepted. [2](#0-1) 

Verification builds the equation `R + cA - sG = 0`. [3](#0-2)  If the supplied public key is `A = 0`, the term `cA` disappears. An attacker can therefore select any scalar `r`, set `R = rG` and `s = r`, and satisfy `R - sG = 0` for every challenge value. `verify` accepts the resulting multiexp result whenever it is identity. [4](#0-3) 

### Impact Explanation
Any verifier that accepts an attacker-controlled Schnorr public key encoded through `Ciphersuite::read_G`, and does not separately reject the identity key, will accept a forged signature under that key. This bypasses the intended proof of possession for the malformed key and can authorize messages or protocol statements bound to that key.

### Likelihood Explanation
The attack requires no private key, secret state, racing, or additional protocol fault. The attacker only needs to supply the identity point as a public key and the corresponding `(R = rG, s = r)` signature. Exploitability is limited to APIs that treat externally supplied identity encodings as usable public keys; valid protocol keys generated through `random_nonzero_F` are not affected.

### Recommendation
Reject identity public keys and identity signature nonces at all public parsing and verification boundaries for `SchnorrSignature`, either inside `SchnorrSignature::read`/`verify` or through a dedicated non-identity public-key type. At minimum, require `!public_key.is_identity() && !signature.R.is_identity()` before evaluating the verification equation.

### Proof of Concept
```text
Let:
  A = identity point encoded canonically
  r = any scalar, for example 1
  R = r * G
  s = r
  c = verifier's challenge binding R, A, and the message
  sig = SchnorrSignature { R, s }

Then:
  R + cA - sG
= rG + c(0) - rG
= 0
```

Because `verify` accepts an identity sum, this signature verifies regardless of the challenge. The same malformed key is reachable from untrusted bytes because `Ciphersuite::read_G` preserves identity rather than rejecting it.

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

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L86-99)
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
```

**File:** crypto/schnorr/src/lib.rs (L102-110)
```rust
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
