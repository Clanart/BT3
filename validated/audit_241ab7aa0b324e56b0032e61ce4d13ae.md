### Title
Missing lower-bound check on `Participant` indexes lets a zero participant index panic the signing path (denial of service) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::view` in `crypto/dkg/src/lib.rs` validates the signing set's size, duplicates, and the *upper* bound of participant indexes (`included.last() > n`), but never validates the *lower* bound. A `Participant(0)` inside `included` passes every check and then panics via unchecked indexing — `self.core.verification_shares[i]` (HashMap `Index` impl) and `Interpolation::interpolation_factor` for `Constant` (`c[usize::from(u16::from(i) - 1)]`, which underflows `0 - 1`). This is the same bug class as CVE-2015-7509: a crafted, attacker-controlled input reaching a code path that dereferences/indexes without a validity check, crashing the process.

### Finding Description
`ThresholdKeys::view` (crypto/dkg/src/lib.rs:463-533) performs these checks on `included`:

- `included.len() >= t` and `<= n` (lines 464-472)
- sorted, deduplicated, and "we are participating" (lines 473-485)
- `included.last() <= n` — upper bound only (lines 487-491)

There is no check that `included[0] >= 1` or that every index is nonzero. Then:

- Line 496 / 505: `interpolation_factor` is called per included participant.
- Line 228: `Interpolation::Constant` does `c[usize::from(u16::from(i) - 1)]` — for `i == Participant(0)`, `u16::from(0) - 1` underflows (panic in debug; in release wraps to `65535`, indexing `c[65535]` on an `n`-element vector → out-of-bounds panic).
- Line 502: `self.core.verification_shares[i]` — the map only contains `1..=n` keys (constructed in `ThresholdKeys::read` at lines 620-623 and enforced by `ThresholdKeys::new` at lines 355-365), so indexing with `Participant(0)` panics via `HashMap`'s `Index` impl, for **both** interpolation variants.

`Participant` is a public-field tuple struct (`(1 ..= n).map(Participant)` at lib.rs:621 and `Participant::new` at dkg/src/lib.rs) — nothing in the type prevents `Participant(0)` when it arrives via deserialization of `HashMap<Participant, _>` keys in DKG/FROST messages (`pedpop`/`musig`/`frost` preprocess and share maps keyed by `Participant`). FROST's `sign` (crypto/frost/src/sign.rs:290-313) builds `included` directly from the attacker-influenced `preprocesses` map keys, checks only `included.last() > n` (line 302), and then calls `self.params.keys.view(included).unwrap()` — so a pre-signing-phase message carrying a zero participant index crashes the node before any `FrostError` can be returned. Notably `Participant::new` (returning `None` for 0) exists precisely to reject zero, but the `view`/`sign` path bypasses it.

### Impact Explanation
An unprivileged counterparty in a DKG or FROST signing session can crash any honest participant's process by injecting a `Participant(0)`-keyed preprocess/share entry. The panic aborts signing mid-protocol; repeated across attempts this permanently stalls threshold signing / DKG completion — a remote denial of service, matching the CVE-2015-7509 class (crash via crafted input, availability-only impact).

### Likelihood Explanation
Reachability requires only that a peer-controlled `HashMap<Participant, ...>` key be deserialized without `Participant::new` validation. The codebase already treats `Participant` as an untrusted, deserializable key type (borsh deserialization in `ThresholdParams` explicitly validates it via `ThresholdParams::new`), and the upper-bound check in `view`/`sign` shows the author intended to defend this boundary but missed the lower bound. Every preprocessor message in every signing session exercises this path, so a malicious session participant can trigger it deterministically.

### Recommendation
In `ThresholdKeys::view` (and/or `AlgorithmSignMachine::sign` at frost/src/sign.rs:302), reject `included[0] == Participant(0)` / any index `== 0` alongside the existing `> n` check — e.g., `if u16::from(included[0]) == 0 { Err(DkgError::InvalidParticipant { .. }) }`. Additionally, make `Participant`'s inner field non-constructible except via `Participant::new` (or add a `#[cfg]`-gated raw constructor), and ensure deserialization paths route through `Participant::new` so `0` can never be represented.

### Proof of Concept
```rust
// Construct ThresholdKeys normally (t=2, n=3, we are participant 1).
let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();
let keys: ThresholdKeys<C> = /* valid keys */;
// Attacker supplies a preprocess map whose keys deserialize to Participant(0),
// so `included` becomes [0, 1, <other>] after our own index is pushed.
let included = vec![Participant(0), Participant(1), Participant(2)];
// Passes: len >= t, len <= n, no duplicates, we participate, last (2) <= n.
// Panics at self.core.verification_shares[&Participant(0)] (missing key)
// or, for Interpolation::Constant, at c[usize::from(0u16 - 1)] underflow.
let _ = keys.view(included); // panic
```

Uncertainty: I could not inspect the exact message-deserialization code that produces `HashMap<Participant, _>` from the wire to confirm `Participant(0)` survives a full round trip without a `Participant::new` gate; if every such map's keys are forced through `Participant::new`, the trigger reduces to API misuse rather than a wire-reachable crash. The missing lower-bound check in `view`/`sign` is nonetheless a concrete defect in the validation boundary.