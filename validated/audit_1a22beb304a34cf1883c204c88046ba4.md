### Title
Deserializing `ThresholdKeys` triggers quadratic Lagrange interpolation — attacker-chosen `t`/`n` cause CPU exhaustion from small input - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t`, `n` (both `u16`, up to 65535) and interpolation tag from untrusted bytes. `ThresholdKeys::new` then computes the group key by evaluating `interpolation_factor` for each of `t` participants, and each Lagrange `interpolation_factor` call is O(`t`) field multiplications plus an inversion — total O(`t²`) ≈ 4.3 × 10⁹ field multiplications for `t = 65535`, from an input of only ~2 MB. This is the Serai analog of CVE-2024-1737: cost superlinear in attacker-supplied records keyed to one object.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` (lines 574–632) reads `t`, `n`, `i` and, for `Interpolation::Lagrange`, immediately proceeds to read `n` verification-share points and calls `ThresholdKeys::new`. `ThresholdKeys::new` (lines 349–391) validates `t <= n <= u16::MAX` but imposes no smaller bound, then computes `group_key` over `1 ..= t` by calling `interpolation.interpolation_factor(*i, &t)` per participant. For `Interpolation::Lagrange`, `interpolation_factor` (lines 229–247) iterates over all `t` included participants performing two field multiplications each, plus a field inversion. Total cost is Θ(`t²`) multiplications + `t` inversions, all derived from two attacker-controlled bytes. The same quadratic path is re-triggered on every `ThresholdKeys::view`/`sign` call over a large `included` set.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (explicitly listed as an untrusted-byte sink; e.g., key material exchanged/loaded in DKG recovery, key-refresh, or reshare flows) supplies `t = n = 0xFFFF`, interpolation byte `1` (Lagrange), one scalar, and 65535 encoded points (~2.1 MB). Deserialization then performs ~4 billion field multiplications and 65k inversions inside `ThresholdKeys::new`, tying up the caller's CPU for a prolonged window per message — a cheap asymmetric CPU-amplification DoS mirroring the BIND RR-per-name degradation.

### Likelihood Explanation
Reachable wherever serialized `ThresholdKeys` are accepted from the network or disk under adversarial influence (recovery/reshare/participant-submitted key material). The only prerequisite is that the bytes parse as valid points; all parameters are attacker-chosen and no honest-party cooperation is required. Cost amplification grows quadratically while input size grows only linearly.

### Recommendation
Enforce a protocol-realistic bound on `t`/`n` in `ThresholdParams::new`/`ThresholdKeys::read` (e.g., reject `n` above the maximum validator-set size) before performing interpolation, and/or compute the group key via a single `multiexp` over precomputed Lagrange bases rather than per-participant O(`t`) interpolation.

### Proof of Concept
Serialize `t = 0xFFFF`, `n = 0xFFFF`, `i = 1` (each `u16` LE), interpolation byte `0x01` (Lagrange), one valid `F` scalar, and `n` valid `G` encodings; feed to `<Secp256k1 as Ciphersuite>::read`-based `ThresholdKeys::<Secp256k1>::read`. `ThresholdKeys::new` at `crypto/dkg/src/lib.rs:376-378` then runs `t` × O(`t`) `interpolation_factor` evaluations (`crypto/dkg/src/lib.rs:229-247`), performing ~4.3 × 10⁹ field multiplications. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crypto/dkg/src/lib.rs (L604-631)
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
    .map_err(io::Error::other)
```
