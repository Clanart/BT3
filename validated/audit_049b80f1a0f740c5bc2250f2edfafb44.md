### Title
Deserializing attacker-controlled `ThresholdKeys` triggers quadratic field arithmetic and up to 65,535 field inversions, causing a denial of service - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` trusts a serialized `n` (a `u16` up to 65,535) and then calls `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` for every participant `1..=t`. Each `interpolation_factor` call under `Interpolation::Lagrange` is O(t) field multiplications plus one field inversion, so a single read is O(t²) ≈ 4.3 billion scalar multiplications plus 65,535 inversions — all driven entirely by bytes an unprivileged party supplies.

### Finding Description
- `ThresholdKeys::read` reads `t`, `n`, `i` directly from the byte stream, reads `n` verification-share points, and calls `ThresholdKeys::new(ThresholdParams::new(t, n, i)?, …)` [1](#0-0) .
- `ThresholdParams::new` only checks `t <= n <= u16::MAX` and `i <= n`; nothing bounds the absolute size [2](#0-1) .
- `ThresholdKeys::new` computes `group_key` as `verification_shares[i] * interpolation_factor(*i, &t)` summed over `t = 1..=params.t()` [3](#0-2) .
- With `Interpolation::Lagrange`, each `interpolation_factor` iterates over all `included` (t elements) doing two field multiplications per element and finishes with `denom.invert().unwrap()` — a full modular inversion per participant [4](#0-3) .
- Total cost for one malicious blob: ~t² scalar muls (≈4.3×10⁹ for t=65535) and t inversions — analogous to the OpenSSL advisory where checking a key triggers work super-polynomial in attacker-controlled size.
- The same quadratic pattern repeats in `ThresholdKeys::view`, which computes `interpolation_factor` once for `i` and again for every member of `included` (up to n) [5](#0-4) .

### Impact Explanation
Any service that feeds untrusted bytes into `ThresholdKeys::read` (listed in scope as an untrusted-input entry point) can be stalled for a very long time by a ~2 MB blob declaring `n = 65535` with Lagrange interpolation, matching the advisory's DoS-on-key-check class. The attacker only needs to deliver bytes; no valid key material, shares, or collusion is required — the points don't even need to be consistent, since the expensive interpolation happens before any semantic check of the shares against `group_key`.

### Likelihood Explanation
Reachability is direct: `read` → `ThresholdKeys::new` → `interpolation_factor`. `Participant` is a plain `u16`, so `n` is attacker-controlled up to the type maximum, and all `n` verification-share reads succeed as long as canonical point encodings are provided (cheap for the attacker). Each inversion is ~256 field operations, so the tail `denom.invert()` per participant alone is heavy; combined with the O(t²) multiply loop the delay is unbounded relative to input size. Medium severity per the mirrored CVSS 5.9 class.

### Recommendation
- Cap `n`/`t` in `ThresholdKeys::read`/`ThresholdParams::new` to a protocol-sane maximum (e.g., a few hundred/thousand) before constructing keys.
- Precompute Lagrange denominators once per signing set (batch-invert or incremental numerator/denominator products) to reduce `view`/`new` from O(t²) to O(t).
- Perform the expensive `group_key` derivation lazily or after cheap structural validation so malformed inputs are rejected before O(t²) work.

### Proof of Concept
```rust
// Attacker crafts ~2 MB: valid curve ID header, t = n = 65535, i = 1,
// interpolation byte = 1 (Lagrange), one arbitrary scalar secret_share,
// then 65,535 canonical point encodings.
let mut buf = vec![];
buf.extend(&(C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(&u16::MAX.to_le_bytes()); // t
buf.extend(&u16::MAX.to_le_bytes()); // n
buf.extend(&1u16.to_le_bytes());     // i
buf.push(1u8);                       // Interpolation::Lagrange
buf.extend(<C::F as PrimeField>::ONE.to_repr().as_ref()); // secret_share
for _ in 0..u16::MAX {
    buf.extend(C::generator().to_bytes().as_ref());       // verification_shares
}
// Spins for ~4.3e9 scalar multiplications + 65,535 inversions inside
// ThresholdKeys::new's group_key loop before returning.
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
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

**File:** crypto/dkg/src/lib.rs (L495-507)
```rust
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
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
