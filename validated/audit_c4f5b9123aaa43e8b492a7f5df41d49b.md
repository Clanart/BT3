### Title
FROST binding-factor transcript commits to the stale, pre-offset group key — the scalar offset applied by `ThresholdView` never propagates into ρ derivation - ([File: crypto/frost/src/sign.rs])

### Summary
`AlgorithmSignMachine::sign` builds the FROST `rho` transcript (which seeds every signer's binding factor ρᵢ) by appending `self.params.keys.group_key()` — the base group key of `ThresholdKeys`. However, the actual key being signed for is `ThresholdView::group_key()`, which is the base key **plus the scalar offset** (`offset * G`) that `ThresholdKeys::view` applies to `included[0]`. Analogous to the reported bug — where `updateBondingCurve` updates a stored implementation address but the hardcoded `getOutputPrice` keeps using the old formula — the offset "updates" the effective group key everywhere except in the one derivation whose entire purpose is to bind the session to that key.

Relevant code:
- `crypto/frost/src/sign.rs:362-368`: `rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes())` — stale base key.
- `crypto/frost/src/sign.rs:312`: `let view = self.params.keys.view(included.clone())` — the offset-adjusted view used for everything else.
- `crypto/frost/src/algorithm.rs:208`: `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` — the Schnorr challenge correctly uses the **view's** (offset-adjusted) group key, creating an inconsistency between the two transcripts that are supposed to describe the same key.

### Finding Description
In FROST, ρᵢ = H(i, group_key, msg, {commitments}) exists to bind each signer's nonce contribution to the *specific* group key and message, preventing commitment replay across keys/sessions. Here the updateable component — the per-`ThresholdKeys` scalar offset applied inside `view()` — is invisible to the ρ derivation:

```rust
// sign.rs:362-368
let mut rho_transcript = A::Transcript::new(b"FROST_rho");
rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
rho_transcript.append_message(b"message", C::hash_msg(msg));
rho_transcript.append_message(
  b"preprocesses",
  C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
);
```

`keys.group_key()` is the pre-offset key; `view.group_key()` = base + offset·G is the key the signature verifies against. Two `ThresholdKeys` sharing the same base group key but carrying different offsets (the mechanism used for tweaked/TapTweak-style key re-derivation) produce **byte-identical ρ transcripts** for identical `(participant set, msg, preprocesses)`. The binding factors therefore fail to domain-separate sessions by effective key — an undocumented transcript collision across distinct public keys.

Concretely, an unprivileged participant in two multisig instances that share a base group key with different offsets can supply the same commitments (their preprocess is attacker-chosen public input) and obtain identical ρᵢ for the same message in both keys' sessions. Any safeguard or downstream assumption that ρ commits to the signing key is silently voided by the offset update path.

### Impact Explanation
The binding factor is the sole mechanism binding a preprocess set to a group key. Because the offset does not propagate, identical `(signer index, message, commitment set)` tuples under two different effective group keys collide to identical ρ values. This enables cross-key reuse of binding factors in parallel-session (ROS-style) arrangements whenever a deployment derives tweaked `ThresholdKeys` over a common base key, weakening the unforgeability argument for the threshold signature and, in combination with deterministic nonce derivation (`Commitments::new` keyed by `original_secret_share`, sign.rs:128-132), eroding the isolation the offset was meant to provide between tweaked keys. Medium-High severity: it is a real divergence from the FROST security model, though full exploitation additionally requires the signer to produce signatures under both tweaked keys.

### Likelihood Explanation
Medium. The defect is deterministic — every signing session under an offset key commits ρ to the wrong key. The gap is unconditional in code, but meaningful exploitation requires a deployment that (a) uses scalar offsets/tweaks (supported via `ThresholdKeys::view`, sign.rs:312) and (b) exposes parallel signing sessions over related keys with attacker-controlled preprocesses — a documented, reachable input path.

### Recommendation
Transcript the effective key, not the stored key: change sign.rs:363 to append `view.group_key().to_bytes()` (i.e., bind ρ to the post-offset `ThresholdView` group key). Alternatively, append both the base key and the offset scalar so the ρ transcript commits to the complete key-derivation state. Mirror whichever choice is made into any standalone verification/resimulation of the ρ transcript so signers and aggregators remain consistent.

### Proof of Concept
1. Construct two `ThresholdKeys<C>` objects `K1`, `K2` with the same underlying shares and `group_key() == G_x`, differing only in scalar offsets `o1 ≠ o2` (so `view.group_key()` are two distinct tweaked keys `X1 ≠ X2`).
2. Instantiate `AlgorithmMachine::new(Schnorr::new(T), K1)` and `::new(Schnorr::new(T), K2)`, and run `sign` in both with the same `msg`, the same `included` set, and an attacker-participant supplying identical preprocess bytes in both sessions.
3. Observe that `rho_transcript` in `sign.rs:362-368` is byte-identical across the two executions — it encodes `group_key = G_x` in both, ignoring `o1`/`o2` — so `BindingFactor::calculate_binding_factors` returns identical ρᵢ for all signers, even though the signatures verify under different group keys (`hram` at algorithm.rs:208 uses `X1`/`X2` respectively).
4. Result: the binding factors — the component that should make the sessions key-specific — demonstrably do not reflect the updated (offset) group key, matching the reported class where an update path leaves a critical derivation pinned to the stale value.

Uncertainty note: I verified the inconsistency between `keys.group_key()` (sign.rs:363) and `view.group_key()` (used at algorithm.rs:208). Whether Serai deployments actually instantiate multiple offset-bearing `ThresholdKeys` over one base key, and whether that converts the collision into key-share recovery, depends on integrator usage of the offset/tweak path.