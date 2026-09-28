### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read`/`ThresholdKeys::new` enables CPU-exhaustion DoS from a single crafted key blob - (File: crypto/dkg/src/lib.rs)

### Summary
The Elasticsearch advisory describes a small authenticated input that occupies a worker for disproportionate time (algorithmic-complexity DoS). The Serai analog is `ThresholdKeys::read` (an explicitly in-scope untrusted-bytes entry point): a crafted blob sets interpolation = Lagrange and `n = t = u16::MAX`, after which `ThresholdKeys::new` computes the group key by evaluating a per-participant `interpolation_factor` that is itself O(t) — yielding O(t²) field multiplications plus t field inversions from a ~2 MB input.

### Finding Description
`ThresholdKeys::read` accepts attacker-controlled `t`, `n`, the interpolation variant, `n` scalar/point elements, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` computes `group_key` by calling `interpolation_factor(*i, &t)` for every `i` in `1 ..= t` [2](#0-1) . For `Interpolation::Lagrange`, `interpolation_factor` iterates over the entire `included` set performing two field multiplications per element and ends with a full field inversion [3](#0-2) . The total cost is therefore O(t·n) multiplications plus t inversions, while the input needed is only `n` group encodings (~32–57 bytes each). With `t = n = 65535`, one blob triggers ~4.3×10⁹ field multiplications and 65535 inversions, stalling any single-threaded verifier/signer pipeline processing it. The same quadratic pattern recurs in `ThresholdKeys::view`, which recomputes `interpolation_factor` for every member of `included` [4](#0-3) .

### Impact Explanation
Any component that deserializes or constructs `ThresholdKeys` from party-supplied bytes (listed as a reachable surface: `ThresholdKeys::read`) can be made to burn CPU quadratic in the encoded participant count. A bounded worker handling such a blob is occupied for orders of magnitude longer than the input size warrants, degrading availability of subsequent signing/verification work — the same availability impact class as CVE-2026-72685.

### Likelihood Explanation
`ThresholdParams::new` bounds `t ≤ n ≤ 65535`, `Participant` is a non-zero `u16`, and `ThresholdKeys::read` places no smaller cap, so the maximum-cost input is trivially constructible and requires no secret knowledge — only the ability to submit bytes to a `read`/`new`/`view` path [5](#0-4) . Reachability depends on an integrator feeding untrusted serialized keys, so severity is Medium rather than High.

### Recommendation
Cap `n`/`t` at deserialization to the maximum validator set size actually supported, or precompute Lagrange denominators once (O(n) batch inversion / prefix-product) so `group_key` derivation is O(n) instead of O(n²). At minimum, reject `Interpolation::Lagrange` blobs where `n` exceeds a protocol bound before entering `ThresholdKeys::new`.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ThresholdKeys::<C>::read:
//   id_len || C::ID || t=0xFFFF || n=0xFFFF || i=1 || interpolation=1 (Lagrange)
//   || secret_share || n * read_G points
// ThresholdKeys::new then runs:
//   for i in 1..=65535 { sum += shares[i] * interpolation_factor(i, all) }
// where each interpolation_factor iterates all 65535 included indexes and
// performs a field inversion -> ~4.3e9 field muls + 65535 inversions
// for a ~2-4 MB input blob.
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

**File:** crypto/dkg/src/lib.rs (L376-379)
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

**File:** crypto/dkg/src/lib.rs (L604-630)
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

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
```
