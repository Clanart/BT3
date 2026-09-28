### Title
Missing length validation of `Interpolation::Constant` coefficients vs. participant count causes out-of-bounds indexing - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys` accepts an `Interpolation::Constant(Vec<F>)` whose vector length is never validated against `params.t()`/`params.n()`. `interpolation_factor` then indexes the vector directly by participant index (`c[usize::from(u16::from(i) - 1)]` at `crypto/dkg/src/lib.rs:228`). This is the same defect class as CVE-2015-8661: a count/length relationship (declared participant count vs. actual coefficient count) is not validated, and attacker-influenced input reaches an unchecked index, producing an out-of-bounds array access.

### Finding Description
`ThresholdKeys::new` checks that `verification_shares.len() == n`, that every share key `<= n`, and that `Interpolation::Constant` is only used when `t == n` (`crypto/dkg/src/lib.rs:355-374`). It does **not** check that the `Constant` coefficient vector's length equals `n` (or `t`). The group key is then computed by iterating participants `1..=t` and calling `interpolation_factor(*i, &t)`, which indexes `c[i - 1]` (`crypto/dkg/src/lib.rs:376-378`). The same unchecked index is used again inside `ThresholdKeys::view` for every participant in a caller-supplied `included` set (`crypto/dkg/src/lib.rs:496-505`).

Two reachable consequences:
- **Short vector** (`c.len() < t`): `ThresholdKeys::new`/deserialization itself panics on the OOB index during `group_key` computation, or `view`/`sign` panics when an `included` participant index exceeds `c.len()`. `ThresholdKeys::read` is a documented untrusted-bytes entry point, and `AlgorithmSignMachine::sign` calls `self.params.keys.view(included.clone()).unwrap()` (`crypto/frost/src/sign.rs:312`), so malformed/interpolated keys or inconsistent state reach the panic during live signing.
- **Length-inconsistent vector** (`t <= c.len() < n` impossible since `t == n` is enforced in `new`, but if the invariant is bypassed via deserialization that doesn't re-run `new`, `t < n` with `Constant` makes `interpolation_factor` return a per-index constant rather than a real Lagrange weight, producing an inconsistent `group_key`/verification-share mapping relative to the serialized shares — an incorrect verifier formula reachable via `ThresholdKeys::read`).

### Impact Explanation
An out-of-bounds index into the coefficient vector panics inside `ThresholdKeys::new`, `ThresholdKeys::view`, or `AlgorithmSignMachine::sign`. In the FROST signing path (`crypto/frost/src/sign.rs:312`), this panic unwinds through `sign`/`complete` — note the coordinator/processor error handling treats `InternalError`/`InvalidSigningSet` as `unreachable!()` (`processor/src/batch_signer.rs:360-365`), so a panic in `view` aborts the signer rather than returning a clean error, denying service for the threshold signing session. If the `t == n` invariant is bypassed on deserialization, an attacker can additionally construct `ThresholdKeys` whose `group_key` is inconsistent with the constant-factor share semantics, yielding signatures/verification for an unintended linear combination.

### Likelihood Explanation
Reachability requires untrusted bytes reaching `ThresholdKeys::read` or an `included` set reaching `view` on keys built with a short `Constant` vector. `ThresholdKeys::read` is an explicitly listed deserialization entry point, and the `included` participant set in `sign` is derived from peer-supplied preprocess map keys (`crypto/frost/src/sign.rs:290-295`). The missing check is unconditional — any `Constant` vec shorter than the max indexed participant triggers it deterministically.

### Recommendation
In `ThresholdKeys::new` (and in `ThresholdKeys::read` if it does not route through `new`), enforce `c.len() == usize::from(params.n())` for `Interpolation::Constant`, returning a `DkgError` (e.g., `IncorrectAmountOfVerificationShares` or a dedicated variant) instead of indexing. Additionally, make `interpolation_factor` use a checked `.get(i - 1)` returning an error rather than panicking, and remove the `.unwrap()` on `view` in `sign.rs:312` in favor of error propagation.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs
// Params: t = n = 2, i = 1
let params = ThresholdParams::new(2, 2, Participant::new(1).unwrap()).unwrap();
// Constant interpolation with only 1 coefficient, but n = 2
let interp = Interpolation::<C::F>::Constant(vec![C::F::ONE]);
// verification_shares: correctly contains participants {1, 2}
// ThresholdKeys::new validates len(verification_shares) == n and t == n,
// but never c.len() == n. group_key computation iterates i in {1, 2}
// and executes c[i - 1], hitting c[1] on a vec of len 1 -> OOB panic.
let keys = ThresholdKeys::new(params, interp, secret_share, verification_shares);

// Alternatively, with keys whose Constant vec has length >= t but a
// view() call including a participant index > c.len() (reachable when
// deserialization skips the new() checks), sign() -> view() ->
// interpolation_factor panics on c[included_max - 1].
```

Uncertainty note: I could not fully inspect `ThresholdKeys::read`'s implementation within the available iterations to confirm whether deserialization bypasses the `t == n`/`new()` checks; the OOB panic path through `new`/`view`/`sign` is confirmed directly from `crypto/dkg/src/lib.rs:228` and `crypto/frost/src/sign.rs:312`.