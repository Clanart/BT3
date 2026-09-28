### Title
Identity public keys accepted during threshold-key deserialization enable forged Schnorr signatures - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts identity verification shares because it uses `Ciphersuite::read_G`, which validates canonical encodings but does not reject the identity point. A serialized threshold key can therefore define an identity group key that `SchnorrSignature::verify` will accept, allowing publicly supplied bytes to produce a key for which signatures can be forged.

### Finding Description
`ThresholdKeys::read` reconstructs verification shares with `<C as Ciphersuite>::read_G(reader)` and passes them to `ThresholdKeys::new` without checking whether any share is the identity point. [1](#0-0) 

`Ciphersuite::read_G` only requires the encoding to decode and canonicalize to the same bytes; unlike `Curve::read_G`, it does not reject identity. [2](#0-1) [3](#0-2) 

`ThresholdKeys::new` derives the group key by interpolating those caller-controlled shares, so a map consisting of identity shares produces an identity group key. [4](#0-3) 

### Impact Explanation
Schnorr verification evaluates `R + cA - sG == 0`. When the public key `A` is identity, the public-key term vanishes for every challenge. [5](#0-4)  Consequently, any scalar `s` paired with `R = sG` satisfies verification under the identity group key. [6](#0-5) 

An unprivileged party who can cause untrusted bytes to be passed to `ThresholdKeys::read` can therefore introduce a semantically invalid threshold key and produce a valid signature under its identity group key without possessing any private key share.

### Likelihood Explanation
The serialized format is public, and participant index `0` is rejected while identity verification shares are not. A forged `ThresholdKeys` object can use `t = n = i = 1`, Lagrange interpolation, and one identity verification share. Because `ThresholdKeys::new` only checks the share count and participant bounds before deriving the group key, the malformed object is accepted. [7](#0-6) 

### Recommendation
Reject identity points for all verification shares in `ThresholdKeys::new` or `ThresholdKeys::read`, preferably by using the stricter non-identity point reader wherever serialized group elements represent public keys. Also validate that `group_key()` is non-identity after derivation and that the local `secret_share` corresponds to `verification_shares[i]`, unless inconsistent serialized state is otherwise excluded by a higher-level protocol.

### Proof of Concept
Conceptually serialize a `ThresholdKeys<C>` object as:

```text
u32_le(C::ID.len()) ||
C::ID ||
u16_le(1) || u16_le(1) || u16_le(1) ||
0x01 ||                    // Interpolation::Lagrange
canonical_scalar_repr(0) ||
canonical_point_repr(identity)
```

`ThresholdKeys::read` accepts this structure: `Participant::new(1)` succeeds, `ThresholdParams::new(1, 1, 1)` succeeds, `C::read_F` accepts zero as canonical, and `C::read_G` accepts a canonical identity encoding. The derived `group_key` is identity.

To forge a signature for that key:

```rust
let s = C::F::random(&mut rng);
let forged = SchnorrSignature {
    R: C::generator() * s,
    s,
};
assert!(forged.verify(C::G::identity(), challenge));
```

The verification equation becomes `sG + c·identity - sG = identity`, independent of `challenge`, so the signature verifies under the deserialized identity group key.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-365)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L620-630)
```rust
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

**File:** crypto/frost/src/curve/mod.rs (L123-130)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
```

**File:** crypto/schnorr/src/lib.rs (L88-99)
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
```

**File:** crypto/schnorr/src/lib.rs (L107-110)
```rust
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```
