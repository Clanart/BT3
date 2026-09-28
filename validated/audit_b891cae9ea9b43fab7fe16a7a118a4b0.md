### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read` enables uncontrolled CPU consumption from attacker-supplied bytes - (File: crypto/dkg/src/lib.rs)

### Summary
The `parse-link-header` ReDoS advisory (CWE-400) is a bug class where a small, attacker-controlled input triggers disproportionate computation. The analog in Serai is `ThresholdKeys::<C>::read`: an attacker supplies serialized `ThresholdKeys` with attacker-chosen `t`/`n` (both `u16`, up to 65535) and `Interpolation::Lagrange`. `ThresholdKeys::new` then computes the group key by calling `interpolation_factor` once per participant `1..=t`, and each call iterates over all `t` included participants — an O(t²) loop with a field inversion per outer iteration. Roughly 2–4 MB of input induces ~4.3·10⁹ field multiplications plus 65535 inversions.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s from the reader, selects `Interpolation::Lagrange` from a single tag byte, reads one secret-share scalar and `n` verification-share points, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdParams::new` only validates `t <= n` and `i <= n`, so an attacker may set `t = n = 65535` [2](#0-1) .

`ThresholdKeys::new` computes `group_key` by summing `verification_shares[i] * interpolation_factor(i, &t)` for all `i` in `1..=t` [3](#0-2) . For `Interpolation::Lagrange`, `interpolation_factor` iterates the entire `included` slice performing two field multiplications per element and finishes with `denom.invert().unwrap()` [4](#0-3) . The total cost is therefore O(t²) multiplications + t inversions, all driven by two attacker-controlled bytes (`t`, `n`) whose only bound is the `u16` maximum.

The byte cost to the attacker is minimal: the Lagrange variant reads no coefficient vector (unlike `Constant`, which reads `n` scalars), so the payload is ~4 bytes of params + 1 tag + 1 scalar + `n` encoded points ≈ 2 MB for `n = 65535`, while the victim performs billions of field operations. This is exactly the CWE-400 shape: computation quadratic in an attacker-chosen parameter with no size limit in `read`.

### Impact Explanation
Any in-scope consumer that calls `ThresholdKeys::read` on untrusted bytes (listed as an acceptable input surface) can be stalled for seconds-to-minutes per message — the work is ~4.3·10⁹ field multiplications and 65535 field inversions for a single ~2 MB input. Repeated submissions amplify this into sustained CPU exhaustion of the parsing party, denying service to a threshold participant without requiring any privilege, key material, or protocol role — only the ability to deliver bytes to `read`.

### Likelihood Explanation
Reachability requires only that serialized `ThresholdKeys` bytes cross a trust boundary to `ThresholdKeys::read`. No valid shares are needed — the expensive computation occurs inside `ThresholdKeys::new` before any cryptographic check on share correctness, so garbage point/scalar encodings that pass canonical `read_F`/`read_G` suffice. Each `read_G` on a canonical encoding is orders of magnitude cheaper than the interpolation that follows, so the amplification holds even accounting for deserialization cost.

### Recommendation
Cap `t`/`n` to the protocol's real maximum (e.g., `MAX_KEY_SHARES_PER_SET`) inside `ThresholdKeys::read` or `ThresholdParams::new`, and/or compute `group_key` incrementally so interpolation is O(t) overall rather than O(t²) per-`interpolation_factor` calls. At minimum, bound `n` before the `Vec::with_capacity(n)`/`read_G` loop and before the O(t²) group-key summation.

### Proof of Concept
Serialize a `ThresholdKeys` blob: `C::ID` length + `C::ID`, then `t = 0xFFFF`, `n = 0xFFFF`, `i = 1`, interpolation tag `1` (Lagrange), one canonical nonzero scalar for `secret_share`, then 65535 copies of a canonical encoded generator point for `verification_shares`. Feed it to `ThresholdKeys::<Ristretto>::read`. `ThresholdParams::new` accepts it, and `ThresholdKeys::new` executes ~4.3·10⁹ field multiplications plus 65535 inversions in the `group_key` summation before returning — all from ~2 MB of input and no valid key material.

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
