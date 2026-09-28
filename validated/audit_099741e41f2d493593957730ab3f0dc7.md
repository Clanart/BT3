### Title
Missing share-to-verification-share consistency check in `ThresholdKeys::new` / `ThresholdKeys::read` allows silent key corruption - (File: crypto/dkg/src/lib.rs)

### Summary
The external report concerns a missing permission check (CWE-862): an entry point that fails to verify the caller/input is authorized or well-formed before acting on it. The analog in Serai is `ThresholdKeys::new`, which accepts a `secret_share` and a `verification_shares` map but never checks that `C::generator() * secret_share == verification_shares[params.i()]`. `ThresholdKeys::read` — one of the listed untrusted-bytes sinks — feeds attacker-controlled bytes directly into this constructor after only structural validation (participant count, indexes ≤ n, interpolation discriminant).

### Finding Description
`ThresholdKeys::new` performs these checks only:

1. `verification_shares.len() == params.n()` and every map key `<= n` (`crypto/dkg/src/lib.rs:355-365`).
2. `Interpolation::Constant` requires `t == n` (`crypto/dkg/src/lib.rs:367-374`).
3. `group_key` is computed by interpolating `verification_shares[1..=t]` (`crypto/dkg/src/lib.rs:376-378`).

Critically, `params.i()`'s own entry in `verification_shares` is used *only* when `i <= t` (for the group key) or later during share verification — the provided `secret_share` is never verified against `verification_shares[i]`. `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) deserializes `t`, `n`, `i`, the interpolation method, `secret_share` via `C::read_F`, and all `n` verification shares via `C::read_G`, then passes them straight to `ThresholdKeys::new`.

An attacker who supplies a serialized `ThresholdKeys` blob can therefore:
- Keep `verification_shares[1..=t]` at their honest values, so `group_key()` returns the real, expected group key (the victim sees the correct multisig address and deposits continue).
- Replace `verification_shares[i]` for the victim's index `i > t` with `G * s_fake`, and set `secret_share = s_fake`.

The corrupted keys are internally consistent from every API the victim can observe (`group_key()`, `original_verification_share(i)`, share verification during signing all agree), but the interpolated shares no longer sum to the group secret. Any signing session including participant `i` produces per-participant signature shares that individually verify — `ThresholdView::verification_share(l)` is derived from the same corrupted `verification_shares` (`crypto/dkg/src/lib.rs:500-507`), so share verification passes — yet the aggregate signature fails to verify under `group_key`. The corruption is undetectable without re-running share consistency checks against the original polynomial commitments.

### Impact Explanation
Funds sent to the (correct-looking) group key become unspendable: every signing attempt including the corrupted participant produces shares that pass individual share verification but aggregate to an invalid Schnorr/FROST signature, with no blame attributable — the signature failure is not traced to the corrupted key material. Because `group_key()` still returns the attacker-preserving value, a scanner will continue reporting deposits as received while the multisig is permanently unable to spend them. This matches "funds reported received that are not spendable," and the root cause is precisely a missing validation check on deserialized untrusted input.

### Likelihood Explanation
Reachability is conditional: it requires the victim (or their processor/coordinator stack) to deserialize `ThresholdKeys` from bytes an unprivileged party influenced — the listed `ThresholdKeys::read` sink. Where key material is loaded from authenticated local storage, this is not exploitable; where key blobs are imported, synced, or restored from an untrusted source, a single corrupted write silently bricks the multisig's spending ability while preserving the correct group key. Medium likelihood given the dependency on how integrators source serialized keys, and Medium impact (integrity/availability of funds, no secret leakage).

### Recommendation
In `ThresholdKeys::new`, after validating the map, assert share consistency:

```rust
if (C::generator() * secret_share.deref()) != verification_shares[&params.i()] {
  Err(DkgError::InvalidShare /* new variant */)?;
}
```

This costs one scalar multiplication at construction/deserialization and makes any inconsistency between the claimed secret share and the committed verification shares fail loudly at `ThresholdKeys::read` time, before the corrupted state can be used to sign.

### Proof of Concept
```rust
// Attacker crafts a blob for ThresholdKeys::<C>::read given the victim's
// honest public verification shares V_1..=V_n and group params (t, n, i>t).
let s_fake = <C as Ciphersuite>::F::random(&mut rng);

let mut serialized = vec![];
serialized.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
serialized.extend(C::ID);
serialized.extend(t.to_le_bytes());
serialized.extend(n.to_le_bytes());
serialized.extend(i.to_le_bytes());          // victim index, i > t
serialized.push(1);                          // Interpolation::Lagrange
serialized.extend(s_fake.to_repr().as_ref());
for l in 1 ..= n {
    let v = if l == i {
        C::generator() * s_fake              // corrupted: V_i = G*s_fake
    } else {
        honest_verification_shares[l]        // real public shares elsewhere
    };
    serialized.extend(v.to_bytes().as_ref());
}

// This succeeds: no check that G*secret_share == verification_shares[i]
let keys = ThresholdKeys::<C>::read::<&[u8]>(&mut serialized.as_ref()).unwrap();

// group_key() is computed only from V_1..=V_t, so it equals the REAL group key.
assert_eq!(keys.group_key(), real_group_key);

// Every signing set containing i produces shares that individually verify
// against keys.view(..).verification_share(i) yet the aggregate signature
// fails under group_key: funds at real_group_key are unspendable.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crypto/dkg/src/lib.rs (L500-521)
```rust
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
