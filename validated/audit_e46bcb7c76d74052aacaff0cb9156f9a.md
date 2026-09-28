### Title
Unbounded `ThresholdKeys::read` deserialization enables severe CPU exhaustion - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` trusts the serialized participant count before constructing `ThresholdParams`, then performs Lagrange interpolation whose cost is quadratic in that count. A compact serialized key with `t = n = 65535` forces approximately 4.3 billion field multiplications, 65,535 field inversions, and 65,535 scalar-point multiplications during successful deserialization. [1](#0-0) 

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as unrestricted `u16` values, but does not create or validate `ThresholdParams` until after all input-dependent data has been consumed. [2](#0-1) 

When Lagrange interpolation is selected, the function reads `n` verification shares, with `n` permitted to be `u16::MAX`. [3](#0-2) 

`ThresholdKeys::new` then derives the group key from participants `1..=t`, invoking `interpolation_factor` for every participant. [4](#0-3) 

Each Lagrange `interpolation_factor` call iterates over every other included participant and performs an inversion. [5](#0-4) 

Consequently, `t = n = 65535` causes each of 65,535 interpolation factors to scan 65,534 other participants, making deserialization quadratic in the attacker-controlled participant count. [5](#0-4) 

### Impact Explanation
A request carrying roughly four megabytes of otherwise canonical field and point encodings can force billions of field operations and tens of thousands of expensive inversions and scalar multiplications. Repeated submissions of this input can monopolize CPU resources and deny service without requiring malformed encodings, private-key knowledge, or a valid DKG participant identity. [6](#0-5) 

The condition is amplified because computational work is performed before semantic validation completes inside `ThresholdKeys::new`. [7](#0-6) 

### Likelihood Explanation
`ThresholdKeys::read` is a public deserializer intended for arbitrary `io::Read` inputs, so any application that accepts serialized threshold keys from less-trusted storage, synchronization, migration, or request paths exposes this cost. The attacker only controls ordinary serialized fields and does not need protocol participation, authentication, or malformed points. [8](#0-7) 

The serialized parameters are valid because `ThresholdParams::new` permits any nonzero `t <= n` and `i <= n`, including the maximum representable values. [9](#0-8) 

### Recommendation
Validate `t`, `n`, and `i` through `ThresholdParams::new` immediately after reading them and enforce a practical protocol-level maximum before allocating or processing `n` elements. For Lagrange keys, calculate interpolation factors incrementally or use an algorithm that avoids the current per-participant scan, and reject unexpectedly large participant sets before reading the verification-share array. [10](#0-9) 

### Proof of Concept
The following conceptual payload targets any concrete `C: Ciphersuite` and reaches the quadratic path:

```rust
use std::io::Cursor;
use ciphersuite::{group::{ff::PrimeField, GroupEncoding}, Ciphersuite};
use dkg::ThresholdKeys;

fn malicious_threshold_keys<C: Ciphersuite>() -> Vec<u8> {
    let mut bytes = Vec::new();

    // Curve identifier.
    bytes.extend((C::ID.len() as u32).to_le_bytes());
    bytes.extend(C::ID);

    // t = n = u16::MAX, i = 1.
    bytes.extend(u16::MAX.to_le_bytes());
    bytes.extend(u16::MAX.to_le_bytes());
    bytes.extend(1u16.to_le_bytes());

    // Lagrange interpolation.
    bytes.push(1);

    // Canonical secret share.
    bytes.extend(C::F::ONE.to_repr().as_ref());

    // n canonical verification shares.
    let encoded = C::generator().to_bytes();
    for _ in 0 .. u16::MAX {
        bytes.extend(encoded.as_ref());
    }

    bytes
}

let payload = malicious_threshold_keys::<C>();
let _ = ThresholdKeys::<C>::read(&mut Cursor::new(payload));
```

During `ThresholdKeys::new`, each of the 65,535 Lagrange factors iterates over the other 65,534 participant indexes and performs an inversion, followed by a scalar-point multiplication for that participant. The input therefore produces more than four billion inner-loop field multiplications before returning a syntactically valid `ThresholdKeys`. [5](#0-4) [4](#0-3)

### Citations

**File:** crypto/dkg/src/lib.rs (L164-179)
```rust
impl ThresholdParams {
  /// Create a new set of parameters.
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

**File:** crypto/dkg/src/lib.rs (L230-246)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
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
