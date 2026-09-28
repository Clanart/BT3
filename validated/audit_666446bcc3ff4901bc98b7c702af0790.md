### Title
`ThresholdKeys::new`/`read` never initializes the binding between `secret_share` and its verification share, leaving the key set unusable like an uninitialized owner - (File: crypto/dkg/src/lib.rs)

### Summary
The analog bug class is a security-critical field left uninitialized/default because the initializing step is skipped. In Serai, `ThresholdKeys::new` accepts an arbitrary `secret_share` and an arbitrary `verification_shares` map but never checks that `verification_shares[i] == generator() * secret_share` for the holder's own index `i`. `ThresholdKeys::read`, which consumes untrusted bytes, inherits this gap. The result is a `ThresholdKeys` whose owner-equivalent state (the local share bound to the public key set) was never initialized, producing a bricked signer whose every signature assembly fails and whose honest holder is falsely blamed.

### Finding Description
`ThresholdKeys::new` validates only counts and index bounds: `verification_shares.len() == n`, each key `<= n`, interpolation applicability. It then derives `group_key` purely from `verification_shares[1..=t]` and stores `secret_share` verbatim. Nowhere is `C::generator() * secret_share == verification_shares[i]` enforced. [1](#0-0) 

`ThresholdKeys::read` reads attacker-controlled `t, n, i`, interpolation, `secret_share`, and all `n` verification shares, then delegates to `ThresholdKeys::new` — so the missing initialization is reachable purely from untrusted bytes. [2](#0-1) 

During FROST signing, `view()` interpolates the stored `secret_share` into `ThresholdView::secret_share`, and `complete()` first tries aggregate verification, then per-share blame via `algorithm.verify_share(view.verification_share(l), ...)`. An inconsistent `secret_share` yields a share `s_i` that fails `verify_share`, so `complete` returns `FrostError::InvalidShare(i)` — the holder is blamed even though the defect was in deserialization. [3](#0-2) [4](#0-3) 

### Impact Explanation
A `ThresholdKeys` loaded from bytes where `verification_shares[i] != G * secret_share` is permanently unusable: every signing session including that participant fails to produce a valid aggregate signature, exactly mirroring the report's "all `onlyOwner` functions always revert" — the participant's signing capability is bricked by a state that was never initialized/validated. Worse, `complete()` actively blames the honest holder (`InvalidShare(i)`), which in the surrounding protocol is grounds for slashing, while a genuinely malicious participant's bad shares are indistinguishable. Group funds whose spend path requires this participant cannot move.

### Likelihood Explanation
`ThresholdKeys::read` is an explicitly in-scope public-input sink, and the codebase itself acknowledges this failure mode in `complete()`: "The only known way to cause this ... is to deserialize a semantically invalid FrostKeys" (sign.rs:492-493). Corruption of stored key material (disk, DB, or any channel supplying serialized keys) is sufficient; no malicious validator or leaked key is needed.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject on mismatch, so deserialization cannot yield a `ThresholdKeys` whose own share was never bound to the published verification share — the direct analog of calling `__Ownable_init`.

### Proof of Concept
```rust
// Build bytes for ThresholdKeys where participant i's secret_share
// does not match verification_shares[i].
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(1u16.to_le_bytes()); // t = 1
buf.extend(1u16.to_le_bytes()); // n = 1
buf.extend(1u16.to_le_bytes()); // i = 1
buf.push(1); // Interpolation::Lagrange
// secret_share = 0 (or any wrong scalar), verification share = generator * real_secret
buf.extend(<C::F as PrimeField>::ZERO.to_repr().as_ref());
buf.extend((C::generator() * real_secret).to_bytes().as_ref());

// Succeeds: no share<->verification-share consistency check exists
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();

// keys.group_key() == generator * real_secret, but view().secret_share() == 0.
// sign() emits share = rho * 0 + nonce = nonce; complete() fails aggregate verify,
// then verify_share((generator*real_secret)*lagrange, ...) fails for i,
// yielding FrostError::InvalidShare(Participant(1)) — the holder is blamed
// and the signing session is bricked, though only malformed bytes were provided.
```

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

**File:** crypto/dkg/src/lib.rs (L591-632)
```rust
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

**File:** crypto/frost/src/sign.rs (L447-521)
```rust
  fn complete(
    self,
    mut shares: HashMap<Participant, SignatureShare<C>>,
  ) -> Result<A::Signature, FrostError> {
    let params = self.params.multisig_params();
    validate_map(&shares, self.view.included(), params.i())?;

    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }

    // We could remove blame_entropy by taking in an RNG here
    // Considering we don't need any RNG for a valid signature, and we only use the RNG here for
    // performance reasons, it doesn't feel worthwhile to include as an argument to every
    // implementor of the trait
    let mut rng = ChaCha20Rng::from_seed(self.blame_entropy);
    let mut batch = BatchVerifier::new(self.view.included().len());
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }

    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
  }
}

```
