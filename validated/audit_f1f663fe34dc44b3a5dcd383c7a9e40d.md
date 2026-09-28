### Title
`ThresholdKeys::read` accepts inconsistent secret share / verification shares, producing an unusable or incriminating key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` deserializes a secret share and a map of verification shares, then constructs `ThresholdKeys` via `ThresholdKeys::new`. Neither `read` nor `new` verifies that `C::generator() * secret_share == verification_shares[i]` for the local participant `i`, nor that any verification share is internally consistent. An attacker who can feed crafted bytes to `ThresholdKeys::read` (the accepted untrusted-input surface for this analog: bytes tampered in transit/at rest, mirroring the HTTP-download bug class where a network-position attacker substitutes resource bytes) causes the victim to accept a key set whose `group_key` does not correspond to the secret share the victim will sign with.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation mode, `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` only checks the number and indices of verification shares and the interpolation applicability, then computes `group_key` purely from `verification_shares[1..=t]` [2](#0-1) . The supplied `secret_share` is stored verbatim and never checked against `verification_shares[params.i()]` [3](#0-2) .

Downstream, `ThresholdView` computes the signing share as `interpolation_factor(i) * secret_share` [4](#0-3)  while peers verify the share against the (attacker-chosen) `verification_shares[i]` [5](#0-4) . If the two are inconsistent, every signature share the victim produces fails verification and is attributed to the victim as faulty behavior, or the declared `group_key` is one nobody can sign for.

### Impact Explanation
Two concrete impacts, both reachable with only crafted input bytes:

1. **Funds unspendable**: the attacker crafts bytes whose `verification_shares` interpolate to a valid-looking `group_key` (reported as the multisig address receiving funds), while the embedded `secret_share` is unrelated. The victim's `group_key()` returns the attacker-chosen key, but its signing share is wrong, so the signing set can never produce a valid signature — funds sent to the reported group key are locked.
2. **False blame / slashing of an honest participant**: the attacker sets `verification_shares[i]` to a point ≠ `G * secret_share`. When the victim signs, its shares fail verification against the tampered verification share, and the victim is identified as the faulty party by FROST/PedPoP blame mechanisms — an unprivileged attacker converts untrusted bytes into a framing of an honest validator.

### Likelihood Explanation
Exploitation requires feeding tampered serialization bytes to `ThresholdKeys::read`. This matches the report's threat model (a privileged network position or compromised transport/store substituting downloaded/persisted resources). The code path performs full canonicality checks on each individual field via `C::read_F`/`C::read_G` [6](#0-5)  but performs zero cross-field consistency checks, so well-formed, fully "valid" encodings are accepted. No secret knowledge is needed to craft the input — `verification_shares` are public points. Severity: Medium-High (fund lock / unjustified fatal blame), contingent on a delivery channel for tampered key bytes.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject the input otherwise. Optionally also sanity-check that `group_key` equals the interpolation over all `t` shares in a manner binding the local share. This mirrors PedPoP's runtime check where a participant's own verification share is computed from the secret (`C::generator() * self.secret`) rather than trusted [7](#0-6) .

### Proof of Concept
1. Choose arbitrary `t == n`, e.g., `t = n = 2`, `i = 1`, `Interpolation::Constant([c1, c2])` (or Lagrange).
2. Pick any scalar `x` and set `secret_share = x`. Pick unrelated points `V1, V2` (e.g., `G * y1`, `G * y2` with `y1 ≠ x`) as `verification_shares[1], verification_shares[2]`.
3. Serialize: `u32 len of C::ID` || `C::ID` || `t` || `n` || `i` || interpolation byte + coefficients || `x.to_repr()` || `V1.to_bytes()` || `V2.to_bytes()`.
4. `ThresholdKeys::<C>::read(&mut bytes)` returns `Ok`. `keys.group_key()` = interpolated combination of `V1, V2` — a key the holder cannot sign for, since `keys.view([1,2]).secret_share()` derives from `x` while verifiers check against `V1`. Every signature share the victim emits fails `s·G == R + c·V_i`, and blame machinery attributes the failure to participant `1` — the victim.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-390)
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

**File:** crypto/dkg/src/lib.rs (L494-498)
```rust
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );
```

**File:** crypto/dkg/src/lib.rs (L500-507)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
```

**File:** crypto/dkg/src/lib.rs (L618-631)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L513-520)
```rust
      verification_shares.insert(
        i,
        if i == self.params.i() {
          C::generator() * self.secret.deref()
        } else {
          multiexp_vartime(&exponential::<C>(i, &stripes))
        },
      );
```
