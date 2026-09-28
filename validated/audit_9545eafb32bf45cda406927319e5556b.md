### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` reachable from a small `n` field enables CPU-exhaustion DoS - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
An untrusted `ThresholdKeys` serialization lets the attacker pick `t` and `n` as arbitrary `u16`s up to 65535. `ThresholdKeys::new` then computes the group key via Lagrange `interpolation_factor` for each participant `1..=t`, and each factor is an O(t) product plus one field inversion — yielding O(t²) field multiplications and t inversions triggered by a ~4-byte attacker-controlled prefix. Like CVE-2026-13064's `$jsonSchema`, the cost is disproportionate to the input and not interruptible mid-computation.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the byte stream with no bound beyond `u16`, then reads `n` scalars/points and calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` derives `group_key` by calling `interpolation_factor(*i, &t)` for every participant in `1 ..= t` [2](#0-1) . For `Interpolation::Lagrange`, `interpolation_factor` loops over all `included` (length t) performing field multiplications, then performs one `invert()` [3](#0-2) .

With `t = n = 65535`, a single call performs ~4.3×10⁹ field multiplications and 65535 field inversions — orders of magnitude more work than the ~4 MB input that triggers it. The same applies per-call: `ThresholdView` construction (`view`) also invokes `interpolation_factor` for every included signer [4](#0-3) , so attacker-influenced signing-set sizes multiply the cost again.

### Impact Explanation
A single small serialized `ThresholdKeys` blob causes a long, uninterruptible, CPU-bound computation. On any thread that calls `ThresholdKeys::read`/`ThresholdKeys::new` on received data (deserialization, share verification, recovery), an unprivileged party can stall the thread for a duration quadratic in the claimed participant count, denying service to legitimate DKG/signing work. There is no early abort: the inversion and product loops run to completion.

### Likelihood Explanation
`ThresholdKeys::read` is an explicitly in-scope entry point for untrusted bytes. The attacker only needs to supply `t`, `n`, `i` and `n` syntactically valid field/point encodings — no valid key material or proof is required to reach the quadratic loop, since `ThresholdKeys::new` computes `group_key` before any external check on the resulting keys. `ThresholdParams::new` accepts any `t <= n <= 65535` [5](#0-4) .

### Recommendation
- Enforce a protocol-level maximum on `t`/`n` inside `ThresholdKeys::read` and `ThresholdParams::new` (real multisigs are on the order of hundreds), rather than the implicit `u16` bound.
- Hoist the Lagrange denominator work: compute all interpolation factors in O(t) using the standard prefix/suffix product trick with a single inversion, eliminating the per-participant `invert()`.
- Bound `included.len()` in `ThresholdKeys::view` against a documented constant before computing factors.

### Proof of Concept
Construct a serialization for any in-scope ciphersuite `C`:
1. `u32` = `C::ID.len()`, then `C::ID`.
2. `t = 0xFFFF`, `n = 0xFFFF`, `i = 1` (u16 LE).
3. interpolation byte `1` (Lagrange).
4. one valid `F` encoding (secret share), then 65535 valid `G` encodings (e.g., the generator repeated — only canonical encoding validity is required).
Feed these bytes to `ThresholdKeys::<C>::read`. Execution enters `ThresholdKeys::new` → 65535 calls to `interpolation_factor`, each looping 65535 times plus a field inversion: ~4.3×10⁹ scalar multiplications and 65535 inversions from one input under 5 MB, with no interruption point.

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

**File:** crypto/dkg/src/lib.rs (L500-507)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
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
