### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read`/`new` enables CPU exhaustion from untrusted bytes - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts an attacker-controlled `n` (and `t ≤ n`) as `u16` fields, then calls `ThresholdKeys::new`, which derives `group_key` by evaluating `Interpolation::Lagrange::interpolation_factor` for each of participants `1..=t`. Each factor evaluation is O(`included.len()`) field multiplications plus an inversion, so key construction costs O(t²) field operations — roughly 4.3 billion multiplications for `t = n = 65535` — while the hostile input is only ~2 MB of point encodings.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` from the stream and forwards them to `ThresholdParams::new`, which only enforces `t <= n` and `i <= n` [1](#0-0) . It then reads `n` verification shares and calls `ThresholdKeys::new` [2](#0-1) . `ThresholdKeys::new` computes `group_key` as the sum of `verification_shares[i] * interpolation_factor(i, &t)` over `1 ..= t` [3](#0-2) . For `Interpolation::Lagrange`, each `interpolation_factor` call iterates over every member of `included`, performing two field multiplications per member and one inversion [4](#0-3) . The work is therefore quadratic in `t` while the deserialized input is linear in `n`. The same quadratic pattern repeats in `ThresholdKeys::view`, which calls `interpolation_factor` once per included signer [5](#0-4) .

### Impact Explanation
An unprivileged party who can feed ~2 MB of crafted bytes to `ThresholdKeys::read` (encoded `t = n = 65535`, `i = 1`, `n` valid points, `n` constant/Lagrange data) causes the deserializer to perform ~65,536 inversion-bearing factor evaluations each iterating ~65,536 shares — billions of field operations on a single thread. This is a resource-amplification deserialization DoS: input size grows linearly in `n` while CPU cost grows quadratically. Any service that parses `ThresholdKeys` from storage or wire data controlled by an unauthenticated/cheap-to-authenticate source can be stalled indefinitely; repeated submissions constitute a denial of service. The same gap exists in `view()` for large `included` sets derived from the parsed `n`.

### Likelihood Explanation
Reachability requires only that attacker-controlled bytes reach `ThresholdKeys::read` — an entry point explicitly exercised on untrusted input in Serai's threat model. No signature, valid share semantics, or group membership is needed; `read` performs all expensive interpolation work before any consistency check could reject the structure, and the points merely need to decode (identity points suffice). The cost to the attacker is a single bounded-size message; the cost to the victim is quadratic CPU time. Medium severity: availability impact only, no key or forgery consequence.

### Recommendation
- Bound `t`/`n` in `ThresholdKeys::read`/`ThresholdParams::new` to a protocol-meaningful maximum (e.g., a few hundred), independent of the `u16` width.
- Replace the O(t) per-signer Lagrange factor computation with an O(t) overall prefix/suffix (or batch-inversion) evaluation, removing the quadratic term.
- Alternatively, compute `group_key` lazily or reject `Interpolation`/`verification_shares` shapes inconsistent with `t` before interpolation begins.

### Proof of Concept
```rust
use std::io;
use dkg::{Participant, ThresholdKeys};
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto; // any Ciphersuite

fn main() {
    // Craft a serialized ThresholdKeys with t = n = 65535
    let n: u16 = 65535;
    let mut bytes = vec![];
    // curve ID header
    bytes.extend((Ristretto::ID.len() as u32).to_le_bytes());
    bytes.extend(Ristretto::ID);
    // t, n, i
    bytes.extend(n.to_le_bytes());               // t
    bytes.extend(n.to_le_bytes());               // n
    bytes.extend(1u16.to_le_bytes());            // i (Participant 1)
    bytes.push(1);                               // Interpolation::Lagrange
    // secret_share: 1
    bytes.extend(<Ristretto as Ciphersuite>::F::ONE.to_repr().as_ref());
    // n verification shares: all generator encodings
    for _ in 0..n {
        bytes.extend(Ristretto::generator().to_bytes().as_ref());
    }
    // Triggers O(t^2) Lagrange interpolation inside ThresholdKeys::new
    let _ = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice());
}
```
Sending this ~2 MB payload where `ThresholdKeys::read` is invoked on untrusted data pins a core for an effectively unbounded duration; repeated payloads deny service.

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

**File:** crypto/dkg/src/lib.rs (L229-247)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L500-506)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
```

**File:** crypto/dkg/src/lib.rs (L620-632)
```rust
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
