### Title
Quadratic interpolation burn in `ThresholdKeys::read` / `ThresholdKeys::new` enables low-bandwidth CPU DoS - (File: crypto/dkg/src/lib.rs)

### Summary
The Micrometer advisory's bug class — an unprivileged request triggering disproportionate resource consumption — maps onto Serai's `ThresholdKeys::<C>::read` → `ThresholdKeys::new` path. A serialized `ThresholdKeys` blob carries attacker-chosen `t` and `n` (each up to 65535) and selects `Interpolation::Lagrange`. `ThresholdKeys::new` then computes the group key by evaluating `interpolation_factor` once per participant `1..=t`, and each `interpolation_factor` call iterates over the entire `included` list (`1..=t`), giving O(t²) field multiplications. With `t = n = 65535` that is ~4.3 billion field multiplications triggered by a single blob of only ~2 MB of point/scalar encodings.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)` directly from untrusted bytes, then reads `n` scalars/points and calls `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:591-631`). Inside `ThresholdKeys::new`, the group key is derived as: [1](#0-0) 

which invokes `interpolation_factor` for each of `t` participants. For `Interpolation::Lagrange`, that function loops over the whole `included` slice: [2](#0-1) 

So construction costs `t * (t - 1)` ≈ t² field multiplications. The same quadratic term reappears in `ThresholdKeys::view`, which computes an interpolation factor per included signer (`crypto/dkg/src/lib.rs:500-506`).

The attacker-controlled knobs are `t` and `n` in the serialized blob; the only constraint is `t <= n <= 65535` enforced by `ThresholdParams::new` (`crypto/dkg/src/lib.rs:166-179`). The input needed is `n` field elements for `Interpolation::Constant` coefficients — wait, no: the trigger is `Interpolation::Lagrange` (tag `1`), so the blob needs only `1` tag byte + `1` scalar for `secret_share` + `n` group encodings. For a 32-byte-group ciphersuite that is ~2 MB of attacker bytes producing ~4.3×10⁹ field multiplications plus a multiexp of 65535 terms, an amplification of roughly three orders of magnitude in compute-per-byte.

### Impact Explanation
Any in-scope consumer that feeds untrusted bytes to `ThresholdKeys::read` — key-share restoration, processor/coordinator key loading, or any RPC that accepts serialized `ThresholdKeys` — can be stalled by a single small request. The quadratic interpolation loop and the 65535-term `sum()` group-key multiexp run on the critical deserialization path before any validity check can reject the blob (`ThresholdParams::new` accepts `t=n=65535`). Repeated submissions turn this into a sustained denial of service of the signing/key-management component, matching the CWE-400/CWE-770 uncontrolled-resource-consumption class of the reference advisory. It affects availability only; no secret leakage or forgery.

### Likelihood Explanation
The path is reachable wherever serialized `ThresholdKeys` cross a trust boundary; `ThresholdKeys::read` is explicitly a deserialization entry point for untrusted input. No signature, proof, or cryptographic validity is required of the blob — `ThresholdKeys::new` performs the quadratic work unconditionally for any `t <= n` and a `Lagrange` tag. The limiting factor is how many deployments actually deserialize attacker-controlled `ThresholdKeys` versus locally generated ones; where they do (recoveries, key hand-off between services), a lone unauthenticated sender can trigger it. The work is honest Rust (no panic/abort needed) and scales quadratically up to the `u16` cap on `n`.

### Recommendation
- Impose a hard upper bound on `n`/`t` in `ThresholdKeys::read` (and `ThresholdParams::new` consumers) consistent with the protocol's real validator-set size (~150), rejecting oversized `n` before any allocation or interpolation.
- For Lagrange interpolation, compute all `t` factors in O(t) total via the standard prefix/suffix product trick (accumulate numerator/denominator products and do a single inversion), replacing the per-participant O(t) loop in `Interpolation::interpolation_factor`.
- Alternatively cache precomputed Lagrange coefficients rather than recomputing per call in both `ThresholdKeys::new` and `ThresholdKeys::view`.

### Proof of Concept
Conceptual reproduction against the in-scope crate:

```rust
// crypto/dkg — attacker-serialized blob fed to ThresholdKeys::read
// Layout written by ThresholdKeys::write (crypto/dkg/src/lib.rs:538-561):
let mut blob = Vec::new();
blob.extend(&(C::ID.len() as u32).to_le_bytes());   // curve id len
blob.extend(C::ID);                                // curve id
blob.extend(&65535u16.to_le_bytes());              // t = 65535
blob.extend(&65535u16.to_le_bytes());              // n = 65535
blob.extend(&1u16.to_le_bytes());                  // i = 1 (valid Participant)
blob.push(1u8);                                    // Interpolation::Lagrange tag
blob.extend(<C::F as PrimeField>::ONE.to_repr().as_ref()); // secret_share
for _ in 0 .. 65535 {
    blob.extend(C::generator().to_bytes().as_ref()); // n verification shares (valid encodings)
}
// ThresholdKeys::read(&mut blob.as_slice()) then executes
// 65535 * 65534 field multiplications inside ThresholdKeys::new
// plus a 65535-term group_key multiexp — ~billions of ops for a ~2.1 MB input.
let _ = ThresholdKeys::<C>::read(&mut blob.as_slice());
```

Points of uncertainty: reachability depends on a deployment actually calling `ThresholdKeys::read` on attacker-controlled bytes (the primitives crate treats these as deserializable, e.g., via the `borsh` `ThresholdParams` path at `crypto/dkg/src/lib.rs:200-208`); and `ThresholdKeys::new` with `t=n` forces `Interpolation::Constant` rejection only when `t != n` — with `t = n = 65535` and the Lagrange tag, `InapplicableInterpolation` does not fire (`crypto/dkg/src/lib.rs:367-374`), so the quadratic path proceeds.

### Citations

**File:** crypto/dkg/src/lib.rs (L229-246)
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
