### Title
FROST rho transcript binds the original group key, not the effective (offset) group key used for signing - (File: crypto/frost/src/sign.rs)

### Summary
The gix-transport bug is a stale-check/stale-commitment flaw: authorization is gated on the original URL while the request is actually dispatched to a rewritten destination. The Serai analog lives in the FROST binding-factor transcript: `rho` commits to `ThresholdKeys::group_key()` — the original, pre-offset group key — while the signature share and its challenge are produced under `ThresholdView::group_key()`, which is the effective key after a scalar offset has been applied. The binding factor, whose sole purpose is to bind every session-distinguishing value, omits the very value that distinguishes the signing context.

### Finding Description
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs`), the per-signer binding factors are derived from `rho_transcript`:

```rust
let mut rho_transcript = A::Transcript::new(b"FROST_rho");
rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
rho_transcript.append_message(b"message", C::hash_msg(msg));
rho_transcript.append_message(b"preprocesses", C::hash_commitments(...));
B.calculate_binding_factors(&rho_transcript);
```
(sign.rs lines 361-371)

`self.params.keys.group_key()` is the key stored at `ThresholdKeys` construction. It is never updated. The actual signing and verification, however, run on `view`, produced by `self.params.keys.view(included)` (line 312), and `Schnorr::sign_share` computes the challenge as `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` where `params` is the `ThresholdView` (`algorithm.rs` line 208). When the `ThresholdView` carries a non-zero scalar offset (the mechanism used for key promotion / taproot-style tweaks, where the offset is folded into the first included participant's effective share and into `view.group_key()`), the effective verification key `keys.group_key() + offset·G` differs from what `rho` committed to.

This is the exact shape of the advisory: the security-relevant check/binding consults the original identity (`self.url` ↔ `keys.group_key()`), while the sensitive operation executes against a mutated identity (rewritten URL ↔ offset view key). `rho` therefore does not bind the effective group key, the signing set's offset, or any addendum-derived key mutation — only the stale base key.

Consequences:

- **Undocumented transcript collision across effective keys.** Two signing sessions over the same `included` set, same `msg`, and same preprocesses, but different offsets `o1 ≠ o2`, produce byte-identical `rho_transcript`s and hence identical binding factors and identical `Rs` (`B.nonces(&nonces)`, sign.rs line 383). The effective group key is the only differentiator, and it is absent from the transcript. Nothing in the crate documents that `rho` intentionally excludes the offset; the comment at line 361 says the transcript is "the FROST-expected rho transcript," which per the FROST design must bind all session context.
- **Weakened session binding for the ROS/parallel-session class.** The binding factor exists precisely so that an attacker cannot freely combine commitments across contexts by making each session's `rho` unique to that session's parameters. Sessions that differ only in the effective key share `rho`, collapsing the domain separation FROST assumes between them.

An unprivileged participant reaches this path purely through public inputs: the `preprocesses` map passed to `sign()`, the `msg`, and the use of an offset-bearing `ThresholdKeys`/`ThresholdView` (offsets are externally registerable, e.g., `register_offset` in the Bitcoin wallet machinery).

### Impact Explanation
Every offset-key signing session with a given base key, message, and preprocess set reuses identical binding factors and nonce sums `Rs`. This removes the effective group key from the session's uniqueness constraint. While a full secret-share recovery additionally requires the victim to reuse a preprocess (a documented fatal misuse), the collision itself means the binding-factor domain does not distinguish signatures for `group_key + o1·G` from `group_key + o2·G`: an attacker who collects or manipulates preprocess sets across offset contexts operates inside a single shared rho domain rather than isolated ones, and the resulting signatures are produced under keys the transcript never committed to. This is a Medium-severity cryptographic-context-binding defect: it violates the FROST binding invariant and constitutes an undocumented transcript collision over attacker-influenced inputs.

### Likelihood Explanation
Reachable whenever `ThresholdView` carries a non-zero offset — i.e., in all offset/promotion-based multisig usage (Bitcoin taproot-tweaked keys are the primary consumer). It requires a participant-controlled preprocess map and message, which are the normal inputs to `sign()`. Escalating from the transcript collision to key-share recovery additionally requires nonce reuse, which is already documented as catastrophic, so the practical standalone severity is bounded at Medium.

### Recommendation
Bind the effective group key, not the stored one, in `rho_transcript`: replace `self.params.keys.group_key()` with `view.group_key()` at sign.rs line 363 so `rho` commits to the key actually being signed under (including any offset). Optionally also append the view's scalar offset explicitly. This mirrors the advisory's suggested fix — validate/commit to the effective destination rather than the original.

### Proof of Concept
Sketch (conceptual, mirroring the advisory's structure):

```rust
// Two views over the same keys with different offsets
let keys: ThresholdKeys<C> = ...;
let mut keys_o1 = keys.clone(); keys_o1.offset(offset_1); // hypothetical offset setter
let mut keys_o2 = keys.clone(); keys_o2.offset(offset_2);

// Same msg, same participant set, same preprocesses map
let (_, pp) = machine_o1.preprocess(&mut rng);
let mut preprocesses = collected_from_peers();

let (sm1, share1) = sign_machine(keys_o1).sign(preprocesses.clone(), msg)?;
let (sm2, share2) = sign_machine(keys_o2).sign(preprocesses, msg)?;

// The rho transcripts inside both sign calls are byte-identical because
// sign.rs:363 transcripts keys.group_key() (identical) rather than
// view.group_key() (different). Rs = B.nonces(&nonces) is therefore identical,
// while hram() in sign_share challenges different effective keys —
// the same divergence class as "auth checked on original URL,
// request sent to rewritten URL."
```

Exact lines: `crypto/frost/src/sign.rs:362-371` (rho transcript built from `keys.group_key()`), `crypto/frost/src/sign.rs:312` (view creation), `crypto/frost/src/algorithm.rs:208` (`hram` over `params.group_key()`, the offset view key).

One uncertainty I could not fully resolve within the available iterations: the precise reachability of attacker-chosen offsets depends on how `ThresholdView`/`ThresholdKeys::offset` is driven by the coordinator/processor, which sits outside the in-scope crates. The transcript collision itself, however, is directly verifiable in the cited code.