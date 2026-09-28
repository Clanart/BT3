### Title
Quadratic interpolation loop in `ThresholdKeys::new` reachable via `ThresholdKeys::read` enables CPU-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2018-11507 is a long-loop DoS: an attacker-controlled count drives a disproportionately expensive loop in a parsing path. The Serai analog lives in the trusted-roots/keys deserialization path: `ThresholdKeys::read` accepts attacker-controlled `t`/`n` (both unbounded `u16`s) and then calls `ThresholdKeys::new`, which derives `group_key` by evaluating `interpolation_factor` for each of participants `1..=t`. With `Interpolation::Lagrange`, each `interpolation_factor` call is O(t) scalar multiplications plus a field inversion, making `group_key` derivation O(t²) — up to ~4.3 × 10⁹ scalar multiplications and 65,535 inversions from a single crafted message.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s with no bound beyond `t <= n <= 65535` (`ThresholdParams::new` at `crypto/dkg/src/lib.rs:166-179`), reads `n` verification-share points, and calls `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:625`). `ThresholdKeys::new` computes the group key as `sum(verification_shares[i] * interpolation_factor(i, t_set))` for `i` in `1..=t` (`crypto/dkg/src/lib.rs:376-378`). For `Interpolation::Lagrange` — selectable by a single attacker-controlled byte at `crypto/dkg/src/lib.rs:606-615` — `interpolation_factor` iterates over the entire included set performing a scalar multiply per element plus one `invert()` (`crypto/dkg/src/lib.rs:229-247`).

The same O(k²) cost recurs at signing time: `ThresholdView::view` calls `interpolation_factor` once per included participant (`crypto/dkg/src/lib.rs:500-507`), and `AlgorithmSignMachine::sign` calls `view` on attacker-influenced signing sets (`crypto/frost/src/sign.rs:312`). A malicious `n` of 65535 therefore lets each subsequent `sign`/`complete` invocation repeat the quadratic work.

### Impact Explanation
An unprivileged party that can feed bytes to `ThresholdKeys::read` (an explicitly in-scope untrusted-bytes sink) with `t = n = 65535` and Lagrange interpolation forces ~t² ≈ 4.3 billion field multiplications plus tens of thousands of field inversions inside `ThresholdKeys::new` — minutes of single-threaded CPU per message, for a payload of only ~2 MB (65535 encoded points). Where deserialized keys or attacker-influenced signing sets reach `view`/`sign`, the quadratic interpolation cost is paid again per operation, stalling signing and share-verification for honest participants. This is availability loss with a large work-amplification factor, matching the Medium-severity DoS class of the source advisory.

### Likelihood Explanation
Reachability requires an integration path that deserializes `ThresholdKeys` (or constructs `ThresholdParams`/`view` sets) from data an untrusted party controls or can influence — e.g., resumption/recovery flows that read keys supplied by peers — rather than keys produced solely by a local honest DKG. The attack needs no key material, valid signatures, or collusion; it is pure input shape. The main mitigating factor is the `u16` cap on `n`: input size grows linearly (~32 bytes per verification share), so the amplification is quadratic but bounded — roughly 2000× the input size in work — which keeps this in the Medium band rather than High.

### Recommendation
- Impose a hard, documented maximum on `t`/`n` in `ThresholdParams::new` (and on deserialized params) reflecting the real protocol bound (e.g., the coordinator's `MAX_KEY_SHARES_PER_SET`), rather than allowing the full `u16` range.
- Cache Lagrange denominators or compute interpolation factors via a shared-prefix (O(t) total) construction instead of per-participant O(t) recomputation.
- Reject `Interpolation::Constant`/`Lagrange` deserialization in `ThresholdKeys::read` where `n` exceeds the deployment bound before allocating the `n`-element vectors at `crypto/dkg/src/lib.rs:608-623`.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — attacker-controlled byte stream for
// ThresholdKeys::<Ristretto>::read

// 1. C::ID length + ID for Ristretto (passes the curve check at lib.rs:578-588)
let mut buf = vec![];
buf.extend(&(Ristretto::ID.len() as u32).to_le_bytes());
buf.extend(Ristretto::ID);

// 2. t = n = 65535, i = 1  -> passes ThresholdParams::new (t <= n)
buf.extend(&u16::MAX.to_le_bytes()); // t
buf.extend(&u16::MAX.to_le_bytes()); // n
buf.extend(&1u16.to_le_bytes());     // i

// 3. Interpolation::Lagrange tag (skips reading n scalars)
buf.push(1);

// 4. secret_share scalar + n canonical verification-share points
buf.extend(Scalar::ONE.to_repr());
for _ in 0 .. u16::MAX {
  buf.extend(EdwardsPoint::generator().compress().to_bytes()); // canonical Ristretto encodings
}

// ThresholdKeys::read -> ThresholdKeys::new -> group_key derivation runs
// interpolation_factor (O(t) muls + 1 inversion) once per i in 1..=t:
//   ~4.3e9 scalar multiplications + 65535 inversions.
let keys = ThresholdKeys::<Ristretto>::read(&mut &buf[..]).unwrap();
```

Supporting code: deserialization loop and dispatch at `crypto/dkg/src/lib.rs:574-631`; quadratic factor computation at `crypto/dkg/src/lib.rs:376-378` and `crypto/dkg/src/lib.rs:226-249`; repeat cost via `view` at `crypto/dkg/src/lib.rs:500-507` invoked from `crypto/frost/src/sign.rs:312`.