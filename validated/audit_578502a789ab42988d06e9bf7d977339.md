### Title
Quadratic CPU denial of service via attacker-controlled threshold parameters in `ThresholdKeys::read`/`ThresholdKeys::new` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` accepts attacker-supplied `t` and `n` (each a `u16`, up to 65535) and then calls `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` once per participant over a participant list of length `t`. Each `interpolation_factor` call for `Interpolation::Lagrange` loops over all `t` included participants, yielding `O(t²)` field operations — roughly 4.3 billion field multiplications for `t = n = 65535` — from a serialized input of only ~2 MB. This is the same algorithmic-complexity DoS class as CVE-2021-32763 (superlinear work triggered by untrusted input), mapped onto Serai's threshold-key deserialization path, which the scope explicitly lists as reachable with untrusted bytes (`ThresholdKeys::read`).

### Finding Description
- `ThresholdKeys::read` reads `t`, `n`, and `i` directly from the reader with no bound beyond `u16`, then reads `n` field elements (Constant interpolation) or `n` group elements (Lagrange), and calls `ThresholdKeys::new(ThresholdParams::new(t, n, i), ...)`. [1](#0-0) 
- `ThresholdParams::new` only rejects `t == 0`, `n == 0`, `t > n`, and `i > n`; there is no cap on `n`. [2](#0-1) 
- `ThresholdKeys::new` computes `group_key` as `sum(verification_shares[i] * interpolation_factor(*i, &t))` over `t = (1..=params.t())`, i.e. `t` calls to `interpolation_factor`. [3](#0-2) 
- `interpolation_factor` for `Interpolation::Lagrange` iterates over the entire `included` slice (length `t`), performing one field multiply for `num` and one field multiply/subtract for `denom` per element — `O(t)` per call, `O(t²)` total. [4](#0-3) 
- The input cost is linear: `n` point encodings (32 bytes each for Ristretto/ed25519-class curves) ≈ 2 MB at `n = 65535`, while the resulting work is ~`65535² ≈ 4.3 × 10⁹` field multiplications plus inversions, orders of magnitude more expensive than parsing the input. The `Constant` variant is worse relative to input size: it requires `t == n` [5](#0-4)  and still triggers `O(t²)` Lagrange-style cost is avoided, but Constant's factor is an `O(1)` table lookup, so Lagrange is the exploitable path.

### Impact Explanation
Any component that deserializes `ThresholdKeys` from bytes an unprivileged party can supply (the prompt's own reachability list includes `ThresholdKeys::read`) can be made to burn ~billions of field operations per request, stalling the processor/coordinator thread handling it. Repeated submissions amplify the effect. This is a reachable, availability-only denial of service analogous in mechanism and severity (Medium, A:H) to the reference ReDoS.

### Likelihood Explanation
Reachability depends on an integrator feeding untrusted bytes to `ThresholdKeys::read` — within scope per the stated rules — and requires no privileges, keys, or collusion. The input is small (~2 MB) and trivially constructible: any `t = n` large value with valid `Participant i`, `Lagrange` tag, and `n` decodable points. No threshold of honest parties is needed; the cost is paid before any signature or share validity is checked.

### Recommendation
- Impose a sane maximum on `n`/`t` (e.g. the protocol's actual max validator count) inside `ThresholdParams::new` or `ThresholdKeys::read`, rejecting oversized parameters before allocating or interpolating.
- Alternatively, compute the group key via a single multiexp with precomputed Lagrange coefficients (already `O(t²)` scalar work but trivial point work), or skip group-key recomputation in `read` by trusting the provided verification shares only after a cheaper consistency check.
- Add a regression test that deserializes a `ThresholdKeys` blob with `t = n = u16::MAX` and asserts it is rejected (or completes within a bounded operation count).

### Proof of Concept
Construct a byte stream for `ThresholdKeys::<Ristretto>::read`:
1. `u32le(C::ID.len()) || C::ID` (valid curve ID).
2. `t = 0xFFFF`, `n = 0xFFFF`, `i = 1` as `u16le`.
3. Interpolation byte `0x01` (Lagrange).
4. `secret_share`: any canonical 32-byte scalar.
5. `65535` copies of a valid Ristretto point encoding (e.g. the generator, 32 bytes each) — total ≈ 2.1 MB.

`ThresholdKeys::read` accepts all parameters (`ThresholdParams::new(65535, 65535, p1)` succeeds), then `ThresholdKeys::new` evaluates `interpolation_factor` for all 65535 participants, each looping over all 65535 included indexes: ~4.3 billion field multiplications plus 65535 inversions, versus ~2 MB of input — a quadratic CPU blowup fully controlled by the sender.

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

**File:** crypto/dkg/src/lib.rs (L368-371)
```rust
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
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
