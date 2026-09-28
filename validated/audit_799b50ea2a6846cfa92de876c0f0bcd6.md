### Title
Quadratic interpolation during `ThresholdKeys::new` lets crafted `ThresholdKeys::read` input hang the host - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2019-2623 is a low-privileged, network-reachable availability bug: an authenticated-but-unprivileged party sends input that makes the server hang. The analog in Serai's in-scope code is `ThresholdKeys::read`, which accepts a fully attacker-controlled `(t, n, i)` triple and `n` verification shares, then calls `ThresholdKeys::new`. `ThresholdKeys::new` computes the group key by evaluating `interpolation_factor` for each of the `t` participants `1 ..= t`, and `interpolation_factor` for `Interpolation::Lagrange` iterates over the entire `t`-element participant list — yielding O(t²) field multiplications plus `t` field inversions. With `t = n = 65535` (both are `u16` from the wire), deserialization performs roughly 4.3×10⁹ scalar multiplications and ~65k inversions, effectively hanging the thread processing the untrusted bytes.

### Finding Description
In `crypto/dkg/src/lib.rs`:

- `ThresholdKeys::read` (lines 574–632) reads `t`, `n`, `i` as raw `u16`s, an interpolation tag, one secret-share scalar, and `n` group elements, then calls `ThresholdKeys::new`.
- `ThresholdKeys::new` (lines 376–378) builds `t = 1 ..= params.t()` and computes `group_key = Σ verification_shares[i] * interpolation_factor(i, &t)`.
- `Interpolation::interpolation_factor` (lines 229–247) for `Lagrange` loops over every element of `included`, performing ~2 field multiplications per element plus one `invert()`.

So `ThresholdKeys::new` costs O(t²) field multiplications. `t` and `n` are bounded only by `u16::MAX` (`ThresholdParams::new` only enforces `t ≤ n` and `i ≤ n`, lines 166–179). No size or participant-count cap exists in the deserialization path — `Participant` indexes up to 65535 are all valid.

The same quadratic blowup exists in `ThresholdKeys::view` (lines 463–533): it calls `interpolation_factor` once per included signer (line 501–506), so a signing set of size `m` costs O(m²), and `m` can be as large as `n`. For a legitimately large deserialized key set (e.g., one recovered/received from untrusted data), a single `view()` call also stalls.

### Impact Explanation
Any code path that feeds attacker-influenced bytes into `ThresholdKeys::read` — e.g., threshold-key material or recovery payloads relayed between participants — can be made to spin in `ThresholdKeys::new` for an extremely long time on a ~2 MB input (`65535` points × 32 bytes + overhead). This is a complete-availability denial of service of the processing task, matching the CVE's "hang or frequently repeatable crash" impact class (CVSS 5.3, availability-only). No secret material is leaked, but the validator/participant process is livelocked deserializing a single message.

### Likelihood Explanation
Reachability requires an application that deserializes `ThresholdKeys` (or constructs `ThresholdView`s) from data an unprivileged peer can influence — precisely the untrusted-bytes entry points enumerated for this audit. The cost is deterministic: every byte string with `t = n = 0xFFFF`, `i = 1`, `Lagrange` interpolation, and `n` valid points triggers the same O(t²) computation, so the attack is repeatable at will with a single message. No threshold collusion, no timing, no leaked keys required — just public input bytes. Severity is Medium: availability impact only, with a non-trivial (~2 MB) input size and the precondition that the target actually calls `ThresholdKeys::new`/`view` on untrusted parameters.

### Recommendation
Cap `t`/`n` to the protocol's real maximum validator-set size (e.g., `MAX_KEY_SHARES_PER_SET`) inside `ThresholdParams::new` and/or `ThresholdKeys::read`, and reject larger values before building the `1 ..= t` participant vector. Additionally or alternatively, compute Lagrange coefficients with an O(t)-prefix/suffix-product algorithm instead of the O(t²) per-participant recomputation in `interpolation_factor`, so even legitimately large `n` cannot be weaponized.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ThresholdKeys::<C>::read:
let mut buf = vec![];
// curve ID
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
// t = n = u16::MAX, i = 1
buf.extend(0xFFFFu16.to_le_bytes());
buf.extend(0xFFFFu16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
// interpolation = Lagrange (cheaper than Constant: no 65535 scalars needed)
buf.push(1);
// secret_share
buf.extend(C::F::ONE.to_repr());
// 65535 valid encoded points
for _ in 1 ..= 0xFFFFu16 {
    buf.extend(C::G::generator().to_bytes());
}
// ThresholdKeys::read -> ThresholdKeys::new -> O(65535^2) field ops: hang
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```
`interpolation_factor` iterates the 65535-element list for each of the 65535 summands in `group_key` (crypto/dkg/src/lib.rs:376-378, 229-247), producing ~4.3×10⁹ scalar multiplications — a repeatable hang from a single unprivileged input.