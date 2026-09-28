### Title
PedPoP accepts identity PoK public keys, allowing unconditionally forged Schnorr proofs - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
PedPoP commitment messages parse coefficient commitments with `Ciphersuite::read_G`, which validates canonical encodings but permits the identity point. The Schnorr proof-of-knowledge verifier then uses the first commitment as the public key without rejecting identity. Because the verifier checks `R + cA - sG == 0`, an attacker can set `A = identity`, choose any nonzero scalar `s`, and set `R = sG`; the equation passes for every challenge.

### Finding Description
`Commitments::read` reads `t` group elements using `C::read_G` and stores the first as the PoK public key: [1](#0-0) . The generic `Ciphersuite::read_G` enforces canonical point encodings but does not reject identity: [2](#0-1) . During round-one verification, `msg.commitments[0]` is passed directly as the Schnorr public key: [3](#0-2) . `SchnorrSignature::batch_statements` verifies `(1)R + cA + (-s)G == identity`: [4](#0-3) . If `A` is identity, `cA` vanishes, so `R = sG` always satisfies the statement.

### Impact Explanation
An unauthenticated PedPoP participant can submit a commitment message whose first coefficient commitment is the canonical identity point and attach a Schnorr proof without needing a challenge-dependent nonce or satisfying the intended proof-of-knowledge structure. The batch verifier accepts it as a valid commitment message. In Serai's threat model, PedPoP relies on this proof to bind the sender to the first polynomial coefficient; accepting an identity public key bypasses that semantic validation and admits a proof generated independently of the challenge.

### Likelihood Explanation
The malformed object is small and deterministic: the attacker supplies canonical identity encodings for the first commitment and any valid scalar `s`, with `R = sG`. No private key material, collusion, timing condition, or malformed encoding is required. Every PedPoP participant processing public commitment messages through `Commitments::read` reaches this path.

### Recommendation
Use the stricter `Curve::read_G`-style semantic validation for PedPoP commitment points and Schnorr `R` values, or explicitly reject `commitments[0].is_identity()` before queueing the PoK. More generally, public keys used for Schnorr proofs should be checked against identity unless identity is an explicitly supported public key.

### Proof of Concept
For a PedPoP instance over any supported `C: Ciphersuite`:

1. Choose an arbitrary nonzero scalar `s`, e.g. `s = 1`.
2. Let `R = C::generator() * s`.
3. Construct `commitments = [C::G::identity(), ...]` with `t` total canonical points.
4. Construct `sig = SchnorrSignature { R, s }`.
5. Serialize this as an `EncryptionKeyMessage<Commitments<C>>`.

Verification computes:

```text
R + cA - sG
= sG + c*identity - sG
= identity
```

so `msg.sig.batch_verify(...)` accepts the proof despite `A` being identity.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L317-329)
```rust
      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );
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

**File:** crypto/schnorr/src/lib.rs (L86-100)
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
```
