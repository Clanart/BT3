### Title
Unvalidated zero participant index causes out-of-bounds/underflow panic in `Interpolation::interpolation_factor` — ([File: crypto/dkg/src/lib.rs])

### Summary
The PoDoFo report is a NULL-dereference DoS triggered by parsing a crafted file. The Serai analog is an unguarded index panic on the FROST signing path: `AlgorithmSignMachine::sign` in `crypto/frost/src/sign.rs` validates the signer set for duplicates and for indexes greater than `n`, but never rejects `Participant(0)`. `Participant::new` refuses 0, so legitimate construction is impossible — yet `sign()` consumes a `HashMap<Participant, Preprocess>` keyed by externally-supplied participant indexes, and pushes every key into `included`. A party able to register a preprocess under index 0 causes `ThresholdKeys::view` → `Interpolation::interpolation_factor` to compute `c[u16::from(0) - 1]`, i.e. `c[usize::MAX]` (or a u16 underflow panic with overflow checks), unconditionally aborting the signing session.

### Finding Description
- `crypto/frost/src/sign.rs:290-312`: `included` is built from `preprocesses.keys()` plus the local index, sorted, then checked only for `included.len() < t`, `included.last() > n`, and adjacent duplicates. There is no lower-bound check, so `Participant(0)` passes validation.
- `self.params.keys.view(included.clone()).unwrap()` (sign.rs:312) is then invoked *before* `validate_map`, and `view` interpolates every member of `included`.
- `crypto/dkg/src/lib.rs:226-249`: `Interpolation::interpolation_factor` computes `c[usize::from(u16::from(i) - 1)]` for the `Constant` variant. For `i == 0`, `u16::from(i) - 1` underflows (panic under `overflow-checks`, or wraps to `65535` otherwise), and `c` only holds `n` coefficients — an out-of-bounds panic either way.
- Secondary consequence even without `Constant`: with `Lagrange`, `F::from(0)` contributes `num *= 0` for the zero participant, silently corrupting every interpolation factor (no panic, but a wrong signer set / wrong `rho` domain) — reinforcing that index 0 was never meant to reach this code; `crypto/dkg/src/lib.rs:151` and `Participant::new` both treat 0 as invalid.
- The same `included` set later indexes `self.view.verification_share(*l)` via `self.verification_shares[&l]` (`crypto/dkg/src/lib.rs:680-682`), a `HashMap` that only contains keys `1..=n`, so `Participant(0)` would also panic there during the blame phase of `complete` (sign.rs:476-477) even if `view()` were reached by a different route.

### Impact Explanation
Any single participant (or any party able to influence the participant→preprocess map, e.g. a coordinator aggregating preprocess messages that self-label their index) can deterministically crash the local `sign()`/`complete()` call with a Rust panic. In a FFI, `catch_unwind`-less runtime, or embedded wallet this is a process abort; in a threaded processor it kills the signing task and can stall/livelock the multisig, since the poisoned preprocess can be replayed each session. This is the direct analog of the PoDoFo NULL-deref: attacker-supplied structure → unchecked dereference/index → denial of service. Severity Medium, matching the source advisory's DoS-only impact.

### Likelihood Explanation
Reachability requires a preprocess keyed under `Participant(0)` to reach `sign()`. The `Participant` index in `preprocesses` is supplied by the caller's session layer rather than deserialized by `read_preprocess`, so exploitation depends on the integrator forwarding an attacker-chosen index without independently rejecting 0 — precisely the gap the library's own validation (`> n`, duplicates) is meant to cover, making the missing zero check an inconsistent and reachable edge. No cryptographic assumptions, collusion, or invalid curve inputs are needed; a lone malicious or misconfigured signer suffices wherever index labels are taken from peer messages.

### Recommendation
Reject `Participant(0)` in `AlgorithmSignMachine::sign` alongside the existing `> n` check (e.g. error `InvalidParticipant` when `u16::from(included[0]) == 0`), and/or make `Participant`'s inner `u16` private with construction only via `Participant::new` so zero cannot be represented. Defensively, return `Option`/`Result` from `ThresholdView::verification_share`/`original_verification_share` instead of panicking on missing keys (`crypto/dkg/src/lib.rs:672-682`).

### Proof of Concept
```rust
// Attacker registers a preprocess under Participant(0) (constructible wherever the
// caller keys the HashMap from untrusted index labels).
let mut preprocesses = HashMap::new();
preprocesses.insert(Participant(/* raw 0, bypassing Participant::new */), attacker_preprocess);
// plus t-1 other valid preprocesses so included.len() >= t

// Victim calls:
machine.sign(preprocesses, msg);
// sign.rs:312 -> keys.view(included) -> interpolation_factor(Participant(0), included)
// dkg/src/lib.rs:228 -> c[usize::from(u16::from(0) - 1)] -> panic (underflow / index OOB)
```

Uncertainty: I could not verify whether `Participant`'s `u16` field is `pub` outside the crate, nor the exact body of `ThresholdKeys::view` (index limits); if the field is private and all deserialization paths route through `Participant::new`, reachability narrows to integrators that construct `Participant` in-crate or via a `borsh`/serde derive that bypasses `new` — worth confirming, though the missing zero check in `sign()` is a genuine inconsistency regardless.