### Title
Quadratic Lagrange interpolation over attacker-controlled threshold parameters enables CPU exhaustion in `ThresholdKeys::read` — ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to CVE-2021-35515 (an attacker-controlled structure causing disproportionate iteration work), `ThresholdKeys::read` accepts a serialized key set whose `t`/`n` fields are unbounded up to `u16::MAX` and then invokes `ThresholdKeys::new`, which performs Lagrange interpolation with cost `O(t²)` in field operations plus `t` field inversions. A ~2 MB attacker-supplied blob can force billions of scalar multiplications and tens of thousands of inversions, a severe asymmetric CPU denial of service.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the input stream and only enforces `t <= n`, `t != 0`, `n != 0`, `i <= n` via `ThresholdParams::new` [1](#0-0) . `ThresholdParams::new` performs no upper bound check beyond `u16` [2](#0-1) .

It then reads `n` verification shares (each a fixed-size canonical point) and calls `ThresholdKeys::new` [3](#0-2) . Inside `new`, the group key is computed by calling `interpolation_factor` for every one of `t` participants over an `included` list of length `t` [4](#0-3) . Each `Interpolation::Lagrange` factor iterates the full `included` set and performs one field inversion [5](#0-4) .

The result: with `Interpolation::Lagrange` (tag byte `1`) and `t = n = 65535`, the reader performs ~4.3 × 10⁹ scalar multiplications and 65,535 inversions — compared to only ~2 MB of input consumed. The CPU work grows quadratically while the input grows linearly.

### Impact Explanation
`ThresholdKeys::read` is listed among the deserialization entry points reachable with untrusted bytes. An unprivileged party that can cause a node/validator to deserialize attacker-controlled `ThresholdKeys` bytes can stall that thread for an extreme duration (quadratic blowup on a 2 MB payload), degrading or halting consensus, signing, or DKG participation — the same availability impact class as the Compress 7Z codec-list infinite loop, realized here as a polynomial blowup rather than a true infinite loop.

### Likelihood Explanation
Reachability depends on an integrator feeding remote bytes to `ThresholdKeys::read` (e.g., reshare/recovery or coordinator-supplied key material), which the threat model explicitly treats as in scope. The attacker only needs to set `t`/`n` to large values and supply `n` validly-encoded points; the points need not be meaningful. Probability of exploitation is moderate since it requires a path where serialized keys come from an untrusted party, but no cryptographic conditions (valid shares, correct group key) need be satisfied for the quadratic work to occur — `ThresholdKeys::new` performs the interpolation before any semantic validation could reject it.

### Recommendation
Bound `t`/`n` at deserialization time to the protocol's actual maximum validator set size (Serai's real sets are far below `u16::MAX`). Reject parameters exceeding that bound in `ThresholdParams::new` or `ThresholdKeys::read` before any interpolation is attempted. Optionally compute the group key via a linear-time method (e.g., incremental prefix computation of Lagrange coefficients or constant-term-only evaluation) so cost remains `O(t)` even if bounds are later raised.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — attacker-controlled bytes for C = Ristretto
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend([0u8; 32]);              // secret_share (any canonical repr encoding 0)
for _ in 0 .. 65535 {
    buf.extend(C::generator().to_bytes().as_ref()); // verification_shares
}
// ThresholdKeys::read(&mut &buf[..]) performs ~4.3e9 field muls + 65535 inversions
// inside ThresholdKeys::new's group_key computation before returning.
```

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

**File:** crypto/dkg/src/lib.rs (L591-632)
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
  }
```
