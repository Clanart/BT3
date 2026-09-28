### Title
`ThresholdKeys::read` trusts externally supplied key material without consistency checks, enabling full group-key substitution - (File: crypto/dkg/src/lib.rs)

### Summary
The bug class is unsafe processing of attacker-controlled external input (XXE: a parser honoring external entities without validation). The Serai analog is `ThresholdKeys::read`, which deserializes untrusted bytes — an allowed reachable path per scope — into a `ThresholdKeys` without verifying that the serialized `secret_share` corresponds to `verification_shares[i]` or that the verification shares lie on a consistent degree `t-1` polynomial. `ThresholdKeys::new` then derives `group_key` solely by interpolating `verification_shares` for participants `1..=t`, so the external bytes fully dictate the reported group key. [1](#0-0) 

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i`, an interpolation method, a raw `secret_share` scalar, and `n` arbitrary group elements as verification shares, then calls `ThresholdKeys::new` which only checks counts, participant bounds, and interpolation applicability before computing `group_key = Σ_{l=1..=t} verification_shares[l] * λ_l`. [2](#0-1)  No check ever enforces `secret_share * G == verification_shares[i]`, nor that all `n` shares interpolate to the same key — the serialization is fully trusted, exactly as an XXE parser trusts an external entity. An attacker supplying crafted bytes can set `verification_shares` to evaluations of a polynomial whose zero-term is a secret *they* know, making `group_key()` return a key the attacker controls. They can also set inconsistent shares so different `t`-subsets recover different secrets. [3](#0-2) 

### Impact Explanation
Any deserialized `ThresholdKeys` yields a `group_key` driven entirely by attacker bytes: funds sent to that key are spendable by the attacker (they know the underlying secret), or unspendable by the honest set when shares are inconsistent — "funds reported received that are not spendable" and effective key theft. `view()`/`ThresholdView` propagate the corrupt shares into FROST signing, since `secret_share` and `verification_shares` flow directly into share production and verification. [4](#0-3) 

### Likelihood Explanation
Exploitation requires the victim to call `ThresholdKeys::read` on attacker-influenced bytes (key provisioning, backup/restore, or any transport of serialized keys). The format has no integrity protection (no MAC/commitment), so any untrusted channel suffices; no collusion or privileged access is needed to forge the bytes themselves. Severity: High — complete substitution of the threshold group's identity.

### Recommendation
In `ThresholdKeys::read`/`new`, verify `C::generator() * secret_share == verification_shares[i]`, and verify that all `n` verification shares are consistent with a degree `t-1` polynomial (e.g., check that every subset of `t` shares interpolates to the same `group_key`, or equivalently that shares `t+1..=n` lie on the polynomial defined by shares `1..=t`). Additionally authenticate serialized key blobs (version tag + integrity) so externally supplied bytes cannot silently redefine the group key.

### Proof of Concept
1. Attacker picks scalar `x`, sets `f(0)=x`, chooses `t-1` random coefficients → polynomial `f` of degree `t-1`, computes `vs_l = G*f(l)` for `l=1..=n`, and victim share `f(i)`.
2. Serialize via the `ThresholdKeys::write` format: `C::ID`, `t`, `n`, `i`, `0x01` (Lagrange), `f(i)`, then `vs_1..=vs_n`. All encodings are canonical so `read_F`/`read_G` accept them. [5](#0-4) 
3. Victim's `ThresholdKeys::read` succeeds; `group_key() == G*x` — a key whose discrete log only the attacker knows. Any deposit scanned to it is attacker-controlled; alternatively, corrupting a single `vs_l` for `l > t` leaves `group_key` unchanged while breaking share verification/recovery for other signing sets.

### Citations

**File:** crypto/dkg/src/lib.rs (L349-391)
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
  }
```

**File:** crypto/dkg/src/lib.rs (L494-532)
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

    Ok(ThresholdView {
      interpolation: self.core.interpolation.clone(),
      scalar: self.scalar,
      offset: self.offset,
      group_key: self.group_key(),
      secret_share,
      original_verification_shares: self.core.verification_shares.clone(),
      verification_shares,
      included,
    })
```

**File:** crypto/dkg/src/lib.rs (L574-632)
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
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L74-101)
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
  }
```
