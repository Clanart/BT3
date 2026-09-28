### Title
`ThresholdKeys::read` deserializes untrusted key material without verifying secret-share/verification-share consistency, enabling injection of a fully attacker-known group key - (File: crypto/dkg/src/lib.rs)

### Summary
The analog of unsafe config-file deserialization (CWE-502) in Serai is the deserialization of untrusted bytes into trusted cryptographic state. `ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation mode, the secret share, and all `n` verification shares from raw bytes, then passes them to `ThresholdKeys::new`, which performs only structural checks (count, participant indexes ≤ n, `t == n` for `Constant` interpolation). It never checks that the loaded `secret_share` corresponds to `verification_shares[i]`, nor that the verification shares form a polynomial whose threshold key is consistent with anything the holder actually generated. An attacker who supplies this byte blob (the "config file" of the DKG layer) can therefore install arbitrary attacker-constructed key material that the node will happily sign under.

### Finding Description
`ThresholdKeys::read` reads the ciphersuite ID, then `t`, `n`, `i`, an interpolation selector (`0` = `Constant` with `n` scalars, `1` = `Lagrange`), a `secret_share` scalar, and `n` verification-share points via `C::read_G` — which accepts the identity point since the identity rejection lives only in `Curve::read_G` for FROST [1](#0-0) [2](#0-1) . `ThresholdKeys::new` then computes `group_key` purely from `verification_shares[1..=t]` interpolated, with no check that `C::generator() * secret_share == verification_shares[i]` or that the shares lie on a threshold polynomial consistent with the caller's prior DKG [3](#0-2) . In honest flows (PedPoP `calculate_share`), share consistency is enforced by batch verification against committed coefficients, but `read` bypasses all of that [4](#0-3) . A crafted blob can set `secret_share` and all verification shares to a polynomial whose coefficients the attacker chose (e.g., constant term `a`, so `group_key = a·G` for attacker-known `a`), or worse, set `verification_shares[i]` independently of `secret_share` so the node emits shares that pass FROST's per-share check only for attacker-chosen values.

### Impact Explanation
Whoever loads this blob obtains `ThresholdKeys` whose effective group key is fully attacker-controlled (the attacker knows the underlying secret `a` directly, not merely a share). Every FROST signature produced with these keys validates under `a·G`, and the attacker can independently forge signatures for that group key — equivalent to the "arbitrary action via crafted config" impact of the reference advisory, realized here as total compromise of the threshold key. Any funds controlled by that group key (e.g., Bitcoin outputs to the tweaked key) are spendable by the attacker alone. Even absent key theft, inconsistent `secret_share`/`verification_shares` let the attacker dictate arbitrary `group_key` values, causing the node to report/operate under a key unrelated to any real DKG output.

### Likelihood Explanation
Requires local delivery of the serialized key blob (storage restore, config import, backup injection) — the same local-access precondition as the reference `yaml.load` advisory (CVSS `AV:L`). No cryptographic breakage is needed; the exploit is purely the absence of integrity/consistency checks on the deserialized state. Medium likelihood within the local-attacker threat model.

### Recommendation
In `ThresholdKeys::read`/`ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[i]` and reject identity verification shares; additionally bind the blob to an expected group key or context hash (e.g., store and check a commitment to the DKG session) so a substituted blob cannot silently redefine the group's key.

### Proof of Concept
```rust
// Attacker crafts a blob for a known secret `a` on ciphersuite C.
let a = C::F::random(&mut rng);                    // attacker-known secret
// Constant interpolation, t = n = 2, i = 1
let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes());                   // t
blob.extend(2u16.to_le_bytes());                   // n
blob.extend(1u16.to_le_bytes());                   // i = Participant 1
blob.push(0);                                      // Interpolation::Constant
blob.extend(C::F::ONE.to_repr().as_ref());         // binding factor p1
blob.extend(C::F::ONE.to_repr().as_ref());         // binding factor p2
blob.extend(a.to_repr().as_ref());                 // secret_share = a
blob.extend((C::generator() * a).to_bytes().as_ref()); // verification_shares[1]
blob.extend((C::generator() * a).to_bytes().as_ref()); // verification_shares[2]

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap(); // accepted
// keys.group_key() == a*G; attacker knows a outright and can forge any signature
// the node produces shares under, or sign without the node entirely.
```
`ThresholdKeys::new` only checks share count, index bounds, and `t == n` — all satisfied — so the malicious blob deserializes successfully [5](#0-4) .

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

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
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
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-499)
```rust
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```
