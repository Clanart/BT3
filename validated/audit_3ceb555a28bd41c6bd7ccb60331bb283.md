### Title
Unbounded attacker-controlled work in `SchnorrAggregate::read`/`verify` accepts up to 4 billion nonce points with no pre-verification budget - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` deserializes an aggregate Schnorr signature by reading an attacker-controlled `u32` count and then deserializing that many group elements, and `SchnorrAggregate::verify` then allocates `Vec::with_capacity(2 * n + 1)` and runs a `multiexp_vartime` over all `n` statements. Any unprivileged party feeding bytes to this `read`/`verify` path can dictate an arbitrary amount of pre-rejection CPU and memory work, with no length cap, no size budget, and no cheap pre-check before the expensive multiexponentiation — the same bug class as GHSA-qcc3-jqwp-5vh2 (missing resource budget before authentication/verification).

### Finding Description
In `crypto/schnorr/src/aggregate.rs:77-88`, `SchnorrAggregate::read` reads a raw `u32` length and loops `for _ in 0 .. u32::from_le_bytes(len)`, pushing `C::read_G(reader)?` results into `Rs` with no upper bound — `len` can be up to `u32::MAX` (~4.29 billion points).

In `crypto/schnorr/src/aggregate.rs:127-146`, `verify` checks only `self.Rs.len() != keys_and_challenges.len()` (a length match the attacker fully controls), then builds `pairs` with `Vec::with_capacity((2 * keys_and_challenges.len()) + 1)`, derives a transcript `weight()` per signature (each `weight` call performs dozens of field doublings over ~64 challenge bytes, `aggregate.rs:22-65`), and finally calls `multiexp_vartime(&pairs)` over the full set.

The attacker's cost is linear in bytes sent (~32 bytes per point); the verifier's cost is ~2 field-element multiplications plus a `weight()` derivation per point, plus the multiexp. Every byte of work happens before any validity decision: a completely garbage aggregate forces the full allocation and multiexp, which returns `false` only after doing O(n) group operations on attacker-chosen n. There is no analogue of `TRANSACTION_SIZE_LIMIT` (as exists in `coordinator/src/tributary/transaction.rs:286`) or any other budget gating this path.

### Impact Explanation
An unprivileged remote party who can submit an aggregate signature for verification (e.g., any API that accepts a serialized `SchnorrAggregate` and calls `verify`) can force the victim to allocate ~`2n+1` `(scalar, point)` pairs and perform ~`2n` scalar multiplications plus `n` wide-`weight` derivations for arbitrarily large `n` (bounded only by `u32::MAX` and transport limits). Repeated submissions with no concurrency budget degrade or stall the verifying component — a pre-auth availability attack, matching the Medium/CWE-770/CWE-799 class of the reference advisory.

### Likelihood Explanation
Reachable by any party able to supply untrusted bytes to `SchnorrAggregate::read`/`verify`; no keys, session state, or validator status are required, and the bytes need not form a valid signature — the expensive multiexp runs before any check fails. Likelihood depends on how exposed the aggregate-verification entry point is in the deployment; the primitive itself provides no defense.

### Recommendation
- Bound `len` in `SchnorrAggregate::read` to a protocol-sane maximum before looping (compare with `TRANSACTION_SIZE_LIMIT`-style guards used elsewhere).
- In `verify`, reject early when `keys_and_challenges.len()` exceeds a bound, and avoid `Vec::with_capacity` sized by untrusted input before any validation.
- Prefer draining-based reads (read exactly what is needed per statement) so malformed streams fail fast instead of committing the full allocation.

### Proof of Concept
Conceptual, against `crypto/schnorr/src/aggregate.rs`:

```rust
// Attacker bytes: u32::MAX length followed by a stream of valid encodings
// (or simply enough bytes to keep read_G succeeding as long as desired).
let mut payload = Vec::new();
payload.extend(u32::MAX.to_le_bytes());              // ~4.29e9 claimed Rs
payload.extend(std::iter::repeat(valid_point_bytes)  // attacker-chosen n
    .take(n * POINT_LEN));

// Victim path:
let agg = SchnorrAggregate::<C>::read(&mut payload.as_slice()).unwrap();
// keys_and_challenges length is attacker-controlled too, so the len check passes
let keys_and_challenges: Vec<(C::G, C::F)> = vec![(pk, c); n];
// Forces Vec::with_capacity(2n+1), n weight() derivations, and multiexp_vartime(2n+1 pairs)
// on entirely garbage statements before returning false.
let _ = agg.verify(b"dst", &keys_and_challenges);
```

The verifier burns O(n) group/field work and `2n+1`-sized allocation purely at the attacker's discretion, with no authentication or budget check prior to the expensive operation.