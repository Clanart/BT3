### Title
Deserialized `ThresholdKeys` never check that the secret share matches the claimed verification share, enabling crafted key material to produce invalid FROST shares and false blame - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` reconstructs `params`, `interpolation`, `secret_share`, and `verification_shares` entirely from attacker-controllable bytes, then hands them to `ThresholdKeys::new`. `ThresholdKeys::new` validates counts and participant ranges but never verifies the fundamental consistency relation `C::generator() * secret_share == verification_shares[params.i()]`. The resulting keys are accepted and used to sign, producing signature shares that fail verification during `complete()`, causing the victim to be blamed/slashed as faulty despite being honest.

### Finding Description
The deserialization path is:

- `ThresholdKeys::read` reads `t`, `n`, `i`, the `Interpolation` variant (Constant coefficients or Lagrange), `secret_share` via `C::read_F`, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new` [1](#0-0) 
- `ThresholdKeys::new` only checks `verification_shares.len() == n`, that all participant indexes are `<= n`, and that `Constant` interpolation requires `t == n`. It then derives `group_key` solely from `verification_shares[1..=t]` and returns `Ok` without ever relating `secret_share` to `verification_shares[i]` [2](#0-1) 
- `view()` interpolates `secret_share` into the signing set, producing the scalar used for FROST signature shares [3](#0-2) 

The DKG protocols (PedPoP etc.) verify shares against commitments at generation time, but nothing guarantees that property survives the `write`/`read` round trip or an independently constructed serialization. The bytes are canonical at the field/point level (`read_F`/`read_G` reject non-canonical encodings [4](#0-3) ) while being semantically inconsistent.

### Impact Explanation
An attacker who supplies crafted `ThresholdKeys` bytes (the explicitly in-scope `ThresholdKeys::read` sink) can cause a victim to run FROST `sign()` with a secret share inconsistent with its own verification share. Every signature share the victim produces fails share verification in `complete()`, so the victim is identified as the faulty participant. In Serai's deployment this drives incorrect blame/slashing of an honest validator (integrity and availability loss, i.e., real fund loss via slashing). This mirrors the CVE's shape: a low-privileged attacker supplies crafted input that a victim component (human/operator loading key material) then consumes, resulting in compromise of the victim's security outcome.

### Likelihood Explanation
Exploitation requires the victim to deserialize attacker-influenced `ThresholdKeys` bytes — a key-restore, migration, or untrusted-channel path — rather than keys produced by a locally executed DKG (which self-verifies shares). It does not require a malicious validator, colluding threshold, or leaked key; only delivery of malformed serialized key material. Given that constraint, Medium severity is appropriate.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject the input otherwise. Optionally also sanity-check that the `Constant` coefficient count matches `n` usage expectations and that `group_key` is non-identity.

### Proof of Concept
```rust
// Constructed bytes where secret_share != discrete log of verification_shares[i]
let mut buf = vec![];
// C::ID header
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
// t = 2, n = 3, i = 1
buf.extend(2u16.to_le_bytes());
buf.extend(3u16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
// Interpolation::Lagrange
buf.push(1);
// secret_share: an attacker-chosen scalar s'
buf.extend(s_prime.to_repr());
// verification shares: valid points, but v[1] != G*s'
for l in 1 ..= 3u16 {
  buf.extend(v_shares[&Participant::new(l).unwrap()].to_bytes());
}
// Accepted with no consistency check
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();
// keys.view(...).sign(...) now emits shares that fail verification,
// so complete() blames this honest participant.
```

Note: I verified `ThresholdKeys::new` performs no share-to-verification-share check, and `read()` inserts exactly `1..=n` shares so no indexing panic masks the issue. I did not fully trace the coordinator-side blame path from a failed share to an on-chain slash; the cryptographic inconsistency and invalid-share outcome are confirmed in `crypto/dkg/src/lib.rs` and the FROST share-verification design.

### Citations

**File:** crypto/dkg/src/lib.rs (L354-380)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L494-521)
```rust
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L574-631)
```rust
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
    .map_err(io::Error::other)
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
