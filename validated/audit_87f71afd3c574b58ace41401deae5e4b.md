### Title
Quadratic CPU exhaustion via attacker-controlled participant count in `ThresholdKeys::read`/`ThresholdKeys::new` Lagrange interpolation - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to jose4j's large-PBES2-count DoS (CVE-2023-51775), `ThresholdKeys::<C>::read` accepts a serialized `n` (u16) and then `ThresholdKeys::new` computes the group key by Lagrange-interpolating participants `1..=t`. The interpolation is `O(t²)` scalar operations plus `t` field inversions, driven by a 2-byte parameter, yielding severe work-per-input-byte amplification.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, then reads `n` verification shares and calls `ThresholdKeys::new(ThresholdParams::new(t, n, i)?, ...)`. [1](#0-0) 

Inside `new`, the group key is computed as the sum over `1..=t` of `verification_shares[i] * interpolation_factor(i, &t)`. [2](#0-1) 

For `Interpolation::Lagrange`, each `interpolation_factor` call loops over all `t` included participants performing two field multiplications each, then a field inversion. [3](#0-2) 

So for `t = n = 65535` (both are u16, all values `t <= n` accepted by `ThresholdParams::new`), `new` performs ~4.3 billion scalar multiplications and 65535 scalar inversions plus point-scalar multiplications — orders of magnitude more work than the ~2–4 MB serialized input would suggest. Like the jose4j `p2c` parameter, the cost parameter is a small integer fully controlled by whoever supplies the bytes, with no bound other than u16::MAX.

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (listed in the reachable input surface) causes a single call to burn CPU proportional to `n²` — ~4.3e9 field multiplications for `n = 65535` — before any validity check could reject the keys. Since each verification share is only ~33–65 input bytes, the amplification is roughly `n`× work per byte, exhausting a thread/CPU core for an extended period per message, analogous to the CPU-consumption DoS in the jose4j advisory.

### Likelihood Explanation
Reachability depends on an integrator passing untrusted bytes to `ThresholdKeys::read`, which the in-scope rules explicitly allow. No signature, proof, or secret knowledge is needed — just a well-formed serialization with large `t`/`n` and `n` valid point encodings (e.g., all `generator()`). Invalid points fail early, so the attacker must supply `n` canonical points, keeping input size linear in `n` while work stays quadratic.

### Recommendation
Cap `n`/`t` to a sane protocol bound (e.g., the maximum validator-set size, such as a few hundred) inside `ThresholdParams::new` or `ThresholdKeys::read`, before the `O(t²)` group-key computation. Alternatively, compute `group_key` incrementally or reject `t` values beyond a documented maximum at deserialization time.

### Proof of Concept
```rust
// crypto/dkg: craft a ThresholdKeys serialization with t = n = u16::MAX
let n: u16 = u16::MAX;
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(n.to_le_bytes());          // t = 65535
buf.extend(n.to_le_bytes());          // n = 65535
buf.extend(1u16.to_le_bytes());       // i = 1
buf.push(1);                          // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref()); // secret_share
for _ in 0 .. n {
  buf.extend(C::generator().to_bytes().as_ref()); // all-valid shares
}
// ~2-4 MB input triggers ~4.3e9 scalar muls + 65535 inversions + 65535 point muls
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```

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

**File:** crypto/dkg/src/lib.rs (L620-631)
```rust
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
