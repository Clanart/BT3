### Title
`ThresholdKeys::read` accepts attacker-crafted keys with no consistency/integrity check, yielding an attacker-controlled group key — (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` deserializes `t`, `n`, the participant index, the interpolation mode, the secret share, and all `n` verification shares directly from untrusted bytes, then hands them to `ThresholdKeys::new`, which computes `group_key` by interpolating `verification_shares[1..=t]` — but never checks that `secret_share * G == verification_shares[i]`, nor that the shares or the resulting group key correspond to anything the holder legitimately owns. The serialized object is therefore fully attacker-controlled "object injection": whatever group key the attacker encodes is what the deserializer accepts as its own key set.

### Finding Description
- `ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s (only `i` is range-checked via `Participant::new`), a 1-byte interpolation tag, `n` scalars for `Constant` interpolation, the `secret_share` scalar, and `n` verification-share points, all via `C::read_F`/`C::read_G` which only enforce canonical encodings [1](#0-0) .
- `ThresholdKeys::new` validates only counts and participant ranges, then derives `group_key` as `Σ verification_shares[i] * interpolation_factor(i, [1..=t])` for `i = 1..=t`. It never verifies `C::generator() * secret_share == verification_shares[params.i()]` [2](#0-1) .
- `Participant::new` rejects zero and `ThresholdParams::new` bounds `i <= n`, but nothing binds the secret share to the public material — the public set is arbitrary [3](#0-2) .
- `view()` happily produces a `ThresholdView` under this attacker-chosen `group_key` and hands back `secret_share` scaled by Lagrange factors, so signing machines built on the view produce valid signature shares under the attacker's key [4](#0-3) .

### Impact Explanation
Any path that feeds untrusted bytes into `ThresholdKeys::read` (backup/recovery blobs, coordinator-distributed key packages, peer-supplied key material) lets an attacker inject a threshold-key object whose group key they fully control: they pick `n` arbitrary verification shares so the interpolation at index 0 equals `a * G` for a secret `a` they know, and set `secret_share` to their own share. The victim then operates (address derivation via bitcoin-serai's `Scanner`, FROST `AlgorithmMachine` signing) under a key the attacker owns, so funds "received" to that group key are spendable only by the attacker — an exact analog of the ZoneMinder object injection: crafted serialized input produces a security-relevant object in an attacker-chosen state.

A weaker variant breaks availability: inconsistent `secret_share`/verification share makes the victim's signature shares fail `verify`, so the signing set silently burns a "participant".

### Likelihood Explanation
The format is versioned only by `C::ID` — no MAC, no checksum tying `secret_share` to `verification_shares`, no proof of possession. Any integrator that transmits or stores `ThresholdKeys::serialize()` output where an attacker can substitute bytes (untrusted storage, relayed key packages) is exposed. Exploitation requires only choosing scalar/point encodings; no cryptographic break is needed. Rated Medium-to-High: reachability depends on the integrator actually deserializing keys from a non-fully-trusted source, but the primitive itself performs zero integrity checking, and the outcome (signing/receiving under an attacker's key) is unconditional once consumed.

### Recommendation
In `ThresholdKeys::new` (or at least in `read`), enforce consistency:
- Verify `C::generator() * secret_share == verification_shares[params.i()]` (cheap `multiexp`).
- Document that `read`/`serialize` are not self-authenticating and require an authenticated channel, or add a MAC keyed by context.

### Proof of Concept
```rust
// Attacker constructs a blob that deserializes into ThresholdKeys whose
// group_key the attacker fully controls. t=2, n=2, victim index i=2.

let a = <C as Ciphersuite>::F::random(&mut OsRng);   // attacker master secret
let s2 = <C as Ciphersuite>::F::random(&mut OsRng);   // victim's "share" (attacker-chosen)

// Lagrange at 0 for {1,2}: l1 = 2, l2 = -1
// group_key = V1*2 - V2. Choose V2 = s2*G (consistent or not), V1 = (a*G + V2) * inv(2)
let two_inv = (C::F::ONE + C::F::ONE).invert().unwrap();
let V2 = C::generator() * s2;
let V1 = (C::generator() * a + V2) * two_inv;

let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes()); // t
blob.extend(2u16.to_le_bytes()); // n
blob.extend(2u16.to_le_bytes()); // i = 2
blob.push(1);                    // Lagrange
blob.extend(s2.to_repr().as_ref());
blob.extend(V1.to_bytes().as_ref());
blob.extend(V2.to_bytes().as_ref());

let keys = ThresholdKeys::<C>::read(&mut blob.as_ref()).unwrap();
// keys.group_key() == a * G — attacker knows a, controls this "victim" key set.
assert_eq!(keys.group_key(), C::generator() * a);
// keys.view(&[p1, p2]) then feeds AlgorithmMachine::sign normally,
// producing signatures valid only under the attacker's key.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L166-179)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
  }
```

**File:** crypto/dkg/src/lib.rs (L355-378)
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
```

**File:** crypto/dkg/src/lib.rs (L463-521)
```rust
  pub fn view(&self, mut included: Vec<Participant>) -> Result<ThresholdView<C>, DkgError> {
    if (included.len() < self.params().t.into()) ||
      (usize::from(self.params().n()) < included.len())
    {
      Err(DkgError::IncorrectAmountOfParticipants {
        t: self.params().t,
        n: self.params().n,
        amount: included.len(),
      })?;
    }
    included.sort();
    {
      let mut found = included[0] == self.params().i();
      for i in 1 .. included.len() {
        if included[i - 1] == included[i] {
          Err(DkgError::DuplicatedParticipant(included[i]))?;
        }
        found |= included[i] == self.params().i();
      }
      if !found {
        Err(DkgError::NotParticipating)?;
      }
    }
    {
      let last = *included.last().unwrap();
      if u16::from(last) > self.params().n() {
        Err(DkgError::InvalidParticipant { n: self.params().n(), participant: last })?;
      }
    }

    // The interpolation occurs multiplicatively, letting us scale by the scalar now
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

**File:** crypto/dkg/src/lib.rs (L591-631)
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
```
