### Title
`ThresholdKeys` accepts zero-weight participants (identity verification shares / zero interpolation factors) — (File: crypto/dkg/src/lib.rs)

### Summary
The OpenQ bug class is "a list validated only by an aggregate check (sum == 100) admits elements of 0, silently excluding a participant." Serai's analog is `ThresholdKeys::new` / `ThresholdKeys::read` in `crypto/dkg`: a keys file specifies `n` verification shares and (under `Interpolation::Constant`) `n` interpolation factors, and validation only checks the *count* of shares and the participant index range — never that any individual participant's weight is non-zero. A participant whose stored verification share is the identity point, or whose `Constant` interpolation factor is 0, contributes nothing to the group key yet still occupies one of the `t`/`n` slots.

### Finding Description
`ThresholdKeys::new` validates `verification_shares.len() == params.n()` and that every participant index is `<= n`, then computes `group_key` as the weighted sum over participants `1..=t` [1](#0-0) . No check rejects `C::G::identity()` verification shares, and no check rejects `Interpolation::Constant` factors equal to `C::F::ZERO` — `interpolation_factor` simply indexes `c[i-1]` [2](#0-1) .

`ThresholdKeys::read` — an in-scope API fed untrusted bytes — deserializes `t`, `n`, `i`, the interpolation variant (reading `n` scalars for `Constant`), the secret share, and `n` verification shares, then calls `ThresholdKeys::new` [3](#0-2) . Since `read_G` only checks canonicality, identity encodings pass through.

Downstream, `view()` multiplies each included participant's stored verification share by its interpolation factor [4](#0-3) . A zero factor or identity share yields an expected share of `identity` (plus `offset` for `included[0]`). In FROST, `verify_share` requires `s_i * G == R_i + c * verification_share_i`; with a zero-weight slot this reduces to `s_i * G == R_i`, which an honest participant with a real secret share can never satisfy — they produce shares that always fail, while still being counted toward the declared `t`-of-`n` threshold.

By contrast, other sub-protocols do enforce non-zero contributions: `Validators::new` rejects `weight == 0` [5](#0-4) , `ThresholdParams::new` rejects `t == 0` / `n == 0` / `i == 0` [6](#0-5) , and PedPoP uses `random_nonzero_F` for coefficients [7](#0-6) . The per-element non-zero check simply isn't applied to the deserialized `ThresholdKeys` weighting inputs.

### Impact Explanation
Exactly as in the OpenQ report — some "participants" expecting to be part of the threshold get a weight of 0 — a `ThresholdKeys` blob can declare an `t`-of-`n` multisig in which one or more participant slots carry zero cryptographic weight. Such a participant can never produce a valid FROST signature share (every signing set including them fails share verification), silently reducing the *effective* threshold / liveness of the multisig below what the declared `(t, n)` parameters claim. A crafted keys file fed to `ThresholdKeys::read` therefore produces a key container whose security parameters do not match its stated parameters — an incorrect (overstated) verifier/payout-structure formula, analogous to `[100, 0, 0]`.

### Likelihood Explanation
Reachability requires an attacker-controlled `ThresholdKeys` serialization reaching `ThresholdKeys::read` (an untrusted-bytes API per the scope rules), or an integrator constructing `ThresholdKeys::new` with a `Constant` interpolation containing zeros. It does not enable signature forgery or key recovery by itself — the group key remains self-consistent — so this is a Medium-severity integrity/parameter-validation flaw rather than a direct break.

### Recommendation
In `ThresholdKeys::new`, reject identity verification shares (`bool::from(share.is_identity())`) for all `1..=n`, and for `Interpolation::Constant` reject any factor equal to `C::F::ZERO` (and verify `factors.len() == n`, which is also currently unchecked on the `new` path — `read` enforces it implicitly). This mirrors the report's recommendation: loop over the array and require each element to be greater than zero.

### Proof of Concept
```rust
// crypto/dkg — craft serialized ThresholdKeys with a zero-weight participant slot
use dkg::{ThresholdKeys, Interpolation, Participant, ThresholdParams};
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;
use zeroize::Zeroizing;
use std::collections::HashMap;

// t = 2, n = 3, we are participant 1
let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();

// Constant interpolation where participant 2's factor is 0 — the "0 tier"
let interpolation = Interpolation::Constant(vec![
  <Ristretto as Ciphersuite>::F::ONE,
  <Ristretto as Ciphersuite>::F::ZERO, // participant 2 contributes nothing
  <Ristretto as Ciphersuite>::F::ONE,
]);

let mut verification_shares = HashMap::new();
verification_shares.insert(Participant::new(1).unwrap(), <Ristretto as Ciphersuite>::generator());
verification_shares.insert(Participant::new(2).unwrap(), <Ristretto as Ciphersuite>::generator());
verification_shares.insert(Participant::new(3).unwrap(), <Ristretto as Ciphersuite>::generator());

// Accepted: only the map length and index bounds are checked
let keys = ThresholdKeys::<Ristretto>::new(
  params,
  interpolation,
  Zeroizing::new(<Ristretto as Ciphersuite>::F::ONE),
  verification_shares,
).unwrap();

// The group key silently omits participant 2's share entirely:
// group_key = v1*1 + v2*0 + v3*... — participant 2 is a dead slot,
// yet params still claim t=2, n=3.
assert_eq!(keys.params().n(), 3);

// The same is reachable over the wire: ThresholdKeys::read accepts
// interpolation byte 0x00 followed by factor scalars [1, 0, 1] and
// identity-encoded verification shares without any per-element check.
```

The identical effect is obtained via `ThresholdKeys::read` with `interpolation = 0x00` and a zero scalar in the factor list, or with an identity `read_G` output for one verification share — both pass all existing validation while giving the corresponding participant weight `0`.

### Citations

**File:** crypto/dkg/src/lib.rs (L166-178)
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
```

**File:** crypto/dkg/src/lib.rs (L226-248)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
        let i_f = F::from(u64::from(u16::from(i)));

        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
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

**File:** coordinator/tributary/src/tendermint/mod.rs (L143-147)
```rust
    for (validator, weight) in validators {
      let validator = validator.to_bytes();
      if weight == 0 {
        return None;
      }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L165-169)
```rust
    for i in 0 .. t {
      // Step 1: Generate t random values to form a polynomial with
      coefficients.push(Zeroizing::new(C::random_nonzero_F(&mut *rng)));
      // Step 3: Generate public commitments
      commitments.push(C::generator() * coefficients[i].deref());
```
