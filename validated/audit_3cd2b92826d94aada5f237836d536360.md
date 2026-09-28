### Title
Attacker-supplied `ThresholdKeys` can grant the attacker a valid threshold share / forgeable group key due to missing share-to-verification-share binding - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to CVE-2025-4374 (incorrect privilege assignment at object creation), `ThresholdKeys::read` / `ThresholdKeys::new` accept fully attacker-controlled `t`, `n`, `i`, `secret_share`, and `verification_shares` without ever checking that `verification_shares[i] == G * secret_share`, nor that the `t+1 ..= n` verification shares correspond to a consistent sharing. `group_key` is computed only from shares `1..=t`, so an attacker who hands a victim such a key set grants themselves signing authority on the victim's threshold identity.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs, lines 574-632) deserializes `t`, `n`, `i`, the interpolation method, the `secret_share`, and `n` verification shares entirely from the input byte stream, then calls `ThresholdKeys::new`. `ThresholdKeys::new` (lines 349-391) validates only:

- `verification_shares.len() == n` and all participant indexes `<= n`,
- constant interpolation implies `t == n`,

and then derives `group_key` as the interpolation over `verification_shares[1..=t]` alone (lines 376-378).

There is no check that `C::generator() * secret_share == verification_shares[i]` — i.e., that the deserialized secret share actually belongs to participant `i` — and no consistency check between shares `t+1..=n` and the first `t` shares. An attacker therefore constructs a `ThresholdKeys` blob where:

- `i` is set to the victim's index,
- the attacker picks all `n` secrets, sets `secret_share` to any value, and sets `verification_shares[l] = G * s_l` for secrets `s_l` the attacker knows for `l = 1..=t`.

The victim deserializes this via `ThresholdKeys::read`, obtains a "valid" key set whose `group_key` is fully known to the attacker (the attacker holds `t` valid shares under it), and uses it in FROST signing (`view()` / `sign` in crypto/frost) under the belief it is their threshold identity. Since `view()` interpolates `secret_share` locally and each signer's contribution is verified against `verification_shares[i]`, a mismatch silently drops only the victim's contributions — the attacker needs no victim cooperation to sign. This mirrors the Quay flaw: creating an object (here, a threshold identity) from requester-influenced data assigns the creator privileges (a valid quorum of shares) they should not have.

### Impact Explanation
An attacker able to feed serialized `ThresholdKeys` to a victim (backup/restore import, key-transport, or any flow deserializing untrusted `ThresholdKeys` bytes — explicitly an in-scope untrusted surface) can unilaterally produce valid threshold signatures under the resulting `group_key`. Concretely: key share recovery / forgery of the threshold signature for any message, since the attacker knows `t` independent valid shares of the group secret. Any funds or authority bound to `group_key()` (e.g., via `tweak_keys` in networks/bitcoin/src/wallet/mod.rs) are controllable by the attacker.

### Likelihood Explanation
Requires the victim to deserialize attacker-influenced `ThresholdKeys` bytes and to rely on the resulting `group_key()` as their own. The read path performs curve-ID and participant-index validation, giving a false impression of integrity; the missing `secret_share ↔ verification_share` binding is a concrete gap. Severity Medium per the external report's class.

### Recommendation
In `ThresholdKeys::new`, assert `C::generator() * secret_share == verification_shares[&params.i()]` (rejecting on mismatch). Optionally commit `secret_share`/`verification_shares` to the serialized form's integrity via an authenticated channel, or document that `ThresholdKeys::read` requires authenticated input.

### Proof of Concept
```rust
// Attacker crafts bytes for victim index i = 1, t = 2, n = 3
let t: u16 = 2; let n: u16 = 3;
let s1 = Scalar::random(&mut rng);          // attacker-known share for index 1
let s2 = Scalar::random(&mut rng);          // attacker-known share for index 2
let s3 = Scalar::random(&mut rng);

let mut ser = vec![];
ser.extend((Secp256k1::ID.len() as u32).to_le_bytes());
ser.extend(Secp256k1::ID);
ser.extend(t.to_le_bytes()); ser.extend(n.to_le_bytes());
ser.extend(1u16.to_le_bytes());             // victim's Participant index
ser.push(1u8);                              // Lagrange interpolation
ser.extend(s1.to_bytes());                  // secret_share: attacker knows it too
for s in [s1, s2, s3] {
    ser.extend((Secp256k1::generator() * s).to_bytes()); // verification_shares
}

// Victim imports it
let keys = ThresholdKeys::<Secp256k1>::read(&mut ser.as_slice()).unwrap(); // accepted

// group_key is Lagrange interpolation of verification_shares[1], [2]
// Attacker, knowing s1 and s2, interpolates s1*L1 + s2*L2 and signs as the group
// without the victim — keys.group_key() is a key the attacker fully controls.
```

Root cause: `ThresholdKeys::new` verifies structural bounds only and never binds `secret_share` to `verification_shares[i]` [1](#0-0) , and `ThresholdKeys::read` trusts all fields from the wire [2](#0-1) . `view()` then uses whatever `i`/`secret_share` was supplied [3](#0-2) .

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
