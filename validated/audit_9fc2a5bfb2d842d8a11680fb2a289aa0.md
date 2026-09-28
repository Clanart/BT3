### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` enables CPU-exhaustion via crafted `ThresholdKeys::read` input - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts a fully attacker-controlled `t` and `n` (each a `u16`, up to 65535) and passes them to `ThresholdKeys::new`, which computes the group key by evaluating `Interpolation::Lagrange::interpolation_factor` for every participant `1 ..= t`. Each `interpolation_factor` call is O(t), making `ThresholdKeys::new` O(t²) in field multiplications — reachable purely from untrusted bytes fed to `ThresholdKeys::read`. This is a direct analog of CVE-2022-3283: a small crafted input triggers disproportionate CPU consumption.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the input stream, then reads `n` verification shares and calls `ThresholdKeys::new` [1](#0-0) . In `ThresholdKeys::new`, the group key is computed as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` [2](#0-1) . For `Interpolation::Lagrange` (a single byte `1` in the input selects it), `interpolation_factor` iterates over the full `included` slice performing a field multiplication for the numerator and denominator per entry, plus a field inversion per call [3](#0-2) . The result is `t` inversions and Θ(t²) field multiplications, so with `t = n = 65535` the deserialization performs ~4.3×10⁹ field multiplications and 65535 inversions before any higher-level bound can reject the input. The same quadratic path exists in `ThresholdKeys::view` for signing sets [4](#0-3) .

### Impact Explanation
An unprivileged party that can cause a node to deserialize attacker-supplied `ThresholdKeys` bytes (the `ThresholdKeys::read` API is an explicitly reachable surface for untrusted bytes) can pin a CPU core for an extended period per attempt with only ~2 MB of input, and repeat it indefinitely. In a consensus/coordinator context this stalls signing, DKG handling, or any service that loads key material derived from external data, degrading availability of the multisig.

### Likelihood Explanation
Any deployment where `ThresholdKeys` bytes cross a trust boundary (key import, backup restore, peer-provided key material, or any wrapper that funnels remote bytes into `ThresholdKeys::read`) is exposed. The attacker needs no valid shares, keys, or protocol position — only control of the serialized bytes — matching the GitLab precedent where merely cloning an issue with crafted content caused the DoS.

### Recommendation
Cap `t`/`n` to a sane protocol maximum before performing interpolation inside `ThresholdKeys::new`/`ThresholdKeys::read` (e.g., reject `n` above the maximum validator-set size). Additionally, compute `group_key` incrementally or via a single multiexp using precomputed denominators batch-inverted once (Montgomery's trick), reducing the cost to O(t) inversions → 1 inversion + O(t) multiplications, and apply the same batching in `ThresholdKeys::view`.

### Proof of Concept
Construct a byte stream for `ThresholdKeys::<C>::read` where `C` is any in-scope ciphersuite (e.g., `Secp256k1`/`Ed25519`):

1. `u32` length + bytes equal to `C::ID` (satisfies the curve-ID gate).
2. `t = 0xFFFF`, `n = 0xFFFF`, `i = 0x0001` (all `u16` LE; passes `ThresholdParams::new` since `t <= n`, `i <= n`, both non-zero [5](#0-4) ).
3. Interpolation byte `0x01` → `Interpolation::Lagrange` (no coefficients read).
4. 32 bytes for `secret_share` (`C::read_F`), then `65535` encoded group elements for `verification_shares` keyed `Participant(1) ..= Participant(n)` — roughly `65535 * 33` bytes (~2 MB for Ed25519 compressed points).
5. `ThresholdKeys::new` then evaluates `interpolation_factor` 65535 times, each looping over all 65535 participants — ~4.3×10⁹ `C::F` multiplications plus 65535 field inversions — consuming orders of magnitude more CPU than the ~2 MB input's parse cost, blocking the calling thread.

### Citations

**File:** crypto/dkg/src/lib.rs (L164-179)
```rust
impl ThresholdParams {
  /// Create a new set of parameters.
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
