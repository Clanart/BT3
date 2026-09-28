### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read` enables denial of service via attacker-controlled `n`/`t` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts an attacker-controlled participant count `n` and threshold `t` (both `u16`, up to 65535). When the encoded interpolation byte selects `Interpolation::Lagrange`, `ThresholdKeys::new` computes the group key by calling `Interpolation::interpolation_factor` once per participant in `1..=t`, and each call itself iterates over all `t` included participants. This yields O(t²) field multiplications plus `t` field inversions — ~4.3 billion field multiplications for `t = 65535` — triggered by only ~2 MB of input. This mirrors the Rack CVE-2024-26146 class: carefully crafted input causing parsing/processing time to grow superlinearly.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` directly from the byte stream with only the structural checks `t <= n` and `i <= n` enforced by `ThresholdParams::new` — no upper bound below `u16::MAX` is applied [1](#0-0) [2](#0-1) .

When the interpolation discriminant is `1` (`Interpolation::Lagrange`), `read` then feeds the parameters into `ThresholdKeys::new` [3](#0-2) . Inside `new`, the group key is derived as a sum over `t` verification shares, each multiplied by `interpolation_factor(*i, &t)` [4](#0-3) . For `Lagrange`, `interpolation_factor` performs an inner loop over every element of `included` (length `t`), doing two field multiplications per element, and finishes with a field inversion [5](#0-4) .

Total work: `t` outer iterations × `t` inner iterations = O(t²) field multiplications + `t` inversions + `t` point-scalar multiplications, all before `read` returns. There is no gas, size, or participant-count limit short of 65535.

### Impact Explanation
Any code path that feeds untrusted bytes to `ThresholdKeys::read` (key-share recovery, externally supplied threshold key blobs, or deserialization of DKG output received over the wire) lets an unprivileged sender stall the caller for hours with a sub-3 MB payload (`n = t = 65535`: one byte discriminant + 32-byte secret share + 65535 × 32-byte verification shares). The CPU burn happens synchronously inside `read`, so a validator/processor thread parsing the message is fully blocked — a medium-severity availability impact identical in shape to the Rack header-parsing DoS.

### Likelihood Explanation
The trigger requires only byte-level control of the serialized `ThresholdKeys`: set `t = n = 65535`, interpolation byte `1`, and append `65535` arbitrary group elements (they do not need to be valid shares — the quadratic loop runs before any cryptographic validation of share correctness, and even malformed points still cost full decode + scalar-mul work in the sum). No authentication, collusion, or secret knowledge is needed; cost asymmetry is ~10⁹ field operations per ~2 MB of input.

### Recommendation
Enforce a protocol-appropriate maximum on `n`/`t` inside `ThresholdKeys::read` (or `ThresholdParams::new`) before any interpolation work — e.g., reject `n` exceeding the maximum validator-set size (`MAX_KEY_SHARES_PER_SET`, already used as a bound in `coordinator/src/tributary/transaction.rs`). Alternatively, bound total interpolation work (`t²` operations) before entering the group-key computation, or replace the naive O(t²) Lagrange with a prefix-product O(t) formulation.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::<C>::read DoS
use dkg::ThresholdKeys;
use ciphersuite::Ristretto;

// Build a malicious serialized ThresholdKeys blob.
let mut buf = vec![];
// curve ID header (len + ID for Ristretto)
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
// t = 65535, n = 65535, i = 1
buf.extend(65535u16.to_le_bytes());
buf.extend(65535u16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
// interpolation = Lagrange (discriminant 1)
buf.push(1);
// secret_share: any 32-byte canonical scalar
buf.extend([1u8; 32]);
// verification_shares: n copies of the Ristretto generator encoding
let g = ciphersuite::Ciphersuite::generator(&Ristretto);
let g_bytes = group::GroupEncoding::to_bytes(&g);
for _ in 0 .. 65535u32 {
    buf.extend(g_bytes.as_ref());
}

// ~2.1 MB input -> ~4.3e9 field muls + 65535 inversions + 65535 scalar muls
// inside ThresholdKeys::new -> interpolation_factor(i, &[1..=t]) for each i.
let _keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
```

The hang occurs in `ThresholdKeys::new` at `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` (crypto/dkg/src/lib.rs:376-378), where `interpolation_factor` with `Interpolation::Lagrange` loops over all `t` elements per call (crypto/dkg/src/lib.rs:229-247).

*Note: reachability assumes a caller passes externally influenced bytes to `ThresholdKeys::read` (the rules explicitly list `ThresholdKeys::read` as an in-scope untrusted-byte sink); I did not enumerate a specific production call site feeding it network input, which is the residual uncertainty.*

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

**File:** crypto/dkg/src/lib.rs (L614-631)
```rust
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
