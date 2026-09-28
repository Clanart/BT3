### Title
Attacker-supplied `ThresholdKeys` bytes yield a fully attacker-controlled group key — `ThresholdKeys::read`/`ThresholdKeys::new` never bind the secret share to its verification share nor shares to a consistent polynomial - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to the Shezmu flaw where anyone could mint collateral, an unprivileged party who can feed bytes to `ThresholdKeys::read` can effectively "mint" a threshold key whose group key and all verification shares they control. `ThresholdKeys::new` only checks the share count and that participant indexes are `<= n`; it never verifies that `secret_share * G == verification_shares[params.i()]`, and it derives `group_key` solely from the verification shares of participants `1..=t`. An attacker can therefore write arbitrary `(secret_share, verification_shares)` so the resulting `group_key()` corresponds to a secret the attacker knows outright — deposits to that key are spendable by the attacker alone, bypassing the intended threshold.

### Finding Description
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) the only validations are:

- `verification_shares.len() == n` [1](#0-0) 
- participant indexes `<= n`

Then `group_key` is computed as the interpolated sum of `verification_shares[1..=t]` [2](#0-1) . No check confirms the caller's `secret_share` corresponds to `verification_shares[params.i()]` (compare `dealer::key_gen`, which constructs both consistently from one polynomial, crypto/dkg/dealer/src/lib.rs:51-53). `ThresholdKeys::read` deserializes attacker-controlled `t, n, i`, interpolation constants, `secret_share`, and all `n` verification shares, then calls `ThresholdKeys::new` [3](#0-2) . `view()` likewise never re-validates consistency — it interpolates the supplied secret share and trusts the map [4](#0-3) .

### Impact Explanation
An attacker who supplies or overwrites the serialized key blob (e.g., a key delivered over an unauthenticated channel, or storage an attacker can write) can craft it so `group_key()` is any key whose discrete log they know — e.g., set the `t` Lagrange-interpolated verification shares to commitments of an attacker-chosen polynomial with known constant term. Any funds the victim's stack scans for / receives to `group_key()` are spendable by the attacker unilaterally: the "threshold" property is silently voided, exactly mirroring Shezmu's permissionless-mint collateral. Additionally, an inconsistent `secret_share` produces FROST signature shares that fail or, combined with chosen verification shares, pass verification while being attacker-predictable, enabling key-share-recovery-style attacks against co-signers' views.

### Likelihood Explanation
Reachable by any party able to deliver untrusted bytes to `ThresholdKeys::read` — an in-scope public input per the rules. No collusion, leaked keys, or malicious threshold needed; a single crafted blob suffices. Impact is High (total loss of funds for the minted key); likelihood depends on how the integrator obtains the blob, hence High rather than Critical.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * *secret_share == verification_shares[&params.i()]` and reject otherwise. Consider additionally verifying the interpolation consistency (e.g., that Lagrange interpolation over any other `t`-subset of shares reproduces the same `group_key`) or documenting that callers must only use keys produced by a DKG/`key_gen` with blame verified, never deserialized-from-untrusted-sources keys without authentication.

### Proof of Concept
```rust
// Attacker picks a known "group secret" g and produces shares consistent with it.
// e.g., t = 2, n = 3: choose coefficients a0 = g, a1; compute s_i = a0 + a1*i,
// set verification_shares[i] = G * s_i for all i, secret_share = s_i for victim i.
// Serialize via ThresholdKeys::write format (curve ID, t, n, i, Lagrange tag,
// secret_share, shares 1..=n) and deliver to the victim.
// Victim calls ThresholdKeys::read -> Ok(keys); keys.group_key() == G * g,
// a key the attacker knows. Scanning/receiving to that key credits funds
// the attacker spends alone. No error is ever emitted because the share-vs-
// verification-share binding is unchecked (crypto/dkg/src/lib.rs:349-391).
```

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

**File:** crypto/dkg/src/lib.rs (L493-521)
```rust
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
