### Title
Identity public keys enable universally forged Schnorr signatures - ([File: crypto/schnorr/src/lib.rs](crypto/schnorr/src/lib.rs))

### Summary
`SchnorrSignature::verify` accepts the identity as a public key and the pair `(R = identity, s = 0)` as a valid signature for every challenge. `SchnorrSignature::read` checks only canonical encodings, while `Ciphersuite::read_G` likewise permits the canonical identity point. This leaves a special-value verification case outside the validated domain, analogous to accepting a reserved IP range as a public address.

### Finding Description
`SchnorrSignature::read` deserializes `R` through `C::read_G` and `s` through `C::read_F`, but applies no semantic checks to either value. [1](#0-0)  The generic point reader rejects malformed and non-canonical encodings, but otherwise returns the decoded prime-group point, including the identity. [2](#0-1)  Verification evaluates `R + cA - sG = 0` without rejecting `A = 0`, `R = 0`, or `s = 0`. [3](#0-2)  Consequently, `verify(identity_key, any_challenge)` succeeds for the forged signature `(0_G, 0_F)`. [4](#0-3) 

### Impact Explanation
An unprivileged caller who can provide or select an identity encoded public key can produce a universally valid signature for any message/challenge accepted by that verifier. This is a concrete signature forgery rather than a malformed-encoding issue: the identity encoding is canonical and passes `read_G`. [5](#0-4)  The attack requires no nonce, private key, hash collision, or malformed serialization. [6](#0-5) 

### Likelihood Explanation
The likelihood is medium because exploitation requires a reachable caller or protocol path to treat the identity point as a usable verification key. The code does not establish that policy itself: `read_G` accepts canonical identity encodings and `SchnorrSignature::verify` does not reject them. [2](#0-1)  Where an application independently rejects identity keys before calling `verify`, this issue is not reachable; where it relies on the signature API's advertised strictness, the forgery is deterministic. [7](#0-6) 

### Recommendation
Reject the identity public key, identity nonce commitment, and zero scalar at the Schnorr verification boundary or document these as mandatory caller-side key-validation requirements. Prefer making `SchnorrSignature::read` reject `R = identity`, and making `verify`/`batch_verify` reject `public_key = identity`; additionally consider rejecting `s = 0` for defense in depth. Any batch APIs should apply the same semantic checks before queueing statements. [8](#0-7) 

### Proof of Concept
Conceptually, for any ciphersuite `C`, public input bytes can decode the canonical encodings for identity `0_G` and scalar `0_F`, then call:

```rust
let sig = SchnorrSignature::<C> { R: C::G::identity(), s: C::F::ZERO };
assert!(sig.verify(C::G::identity(), arbitrary_challenge));
```

This succeeds because the verification equation becomes `0_G + c·0_G - 0·G = 0_G` for every challenge. [9](#0-8)

### Citations

**File:** crypto/schnorr/src/lib.rs (L34-41)
```rust
/// A Schnorr signature of the form (R, s) where s = r + cx.
///
/// These are intended to be strict. It is generic over Ciphersuite which is for PrimeGroups,
/// and mandates canonical encodings in its read function.
///
/// RFC 8032 has an alternative verification formula, 8R = 8s - 8cX, which is intended to handle
/// torsioned nonces/public keys. Due to this library's strict requirements, such signatures will
/// not be verifiable with this library.
```

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
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

**File:** crypto/schnorr/src/lib.rs (L117-126)
```rust
  pub fn batch_verify<R: RngCore + CryptoRng, I: Copy + Zeroize>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    id: I,
    public_key: C::G,
    challenge: C::F,
  ) {
    batch.queue(rng, id, self.batch_statements(public_key, challenge));
  }
```

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
