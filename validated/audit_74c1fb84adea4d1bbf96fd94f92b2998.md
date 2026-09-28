### Title
Cached FROST preprocess seed is reused for every sign attempt under the same context, enabling secret share recovery via nonce reuse - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores a deterministic FROST preprocess seed in `CachedPreprocesses` keyed by `self.context`, and reloads it via `AlgorithmSignMachine::from_cache` on **every** signing operation for that context — it is never invalidated or deleted after use. `share_internal` calls `preprocess_internal` each time it runs, so any repeat signing under the same context reuses identical nonces. Per FROST's own documentation in this codebase, reuse of a preprocess/nonce enables third-party recovery of the private key share.

### Finding Description
In `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:100-148), a fresh preprocess is only generated when `CachedPreprocesses::get(self.txn, &self.context)` is `None` (line 123). Afterwards, the cached seed is decrypted and passed to `AlgorithmSignMachine::from_cache` (line 144-145), which deterministically reconstructs the same `SignMachine` — same `seed`, same `nonces`, same published `preprocess.commitments` — because seeded preprocesses derive everything from the RNG seed (crypto/frost/src/sign.rs:171-178, and spec/cryptography/FROST.md:45-62).

`share_internal` (lines 150-181) invokes `self.preprocess_internal(participants).0` unconditionally at line 156. There is no call that deletes or rotates the cache after `machine.sign(...)` consumes it, despite `SignMachine::from_cache`'s contract stating "After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" (crypto/frost/src/sign.rs:216-224).

The analog to the upstream bug (calling `amdgpu_irq_put` on an IRQ that was never acquired — a meaningless teardown on already-invalidated state that produces a call trace) is that the driver-equivalent teardown/invalidation of the consumed preprocess is missing: the consumed one-time secret is silently re-fetched and reused instead of being dropped, turning a should-be-fatal misuse into a quiet secret leak rather than a benign trace.

### Impact Explanation
If the same context is signed twice with different signing sets or a different effective message/`rho` (e.g., a retry, a parallel session, or a peer-driven re-attempt where a malicious participant submits different preprocesses), the signer emits two shares built on the same nonce scalars `d + e·rho`. With different binding factors or challenge `c`, two shares `s1 = d + e·ρ1 + c1·λ·x` and `s2 = d + e·ρ2 + c2·λ·x` allow an observer to solve for the Lagrange-weighted secret share `λ·x` — i.e., recovery of this validator's private key share, and with threshold-many compromised shares, the group key. This is the classic FROST nonce-reuse / ROS-style key-recovery attack.

### Likelihood Explanation
Reuse requires `share_internal` (or `preprocess_internal`) to execute more than once per `context`. That happens on any signing retry, re-execution after crash recovery (the cache is persisted in `DbTxn`, so a restart mid-protocol reuses the same seed deterministically), or whenever multiple sign attempts share a `context` value. An unprivileged participant who can influence preprocess sets or trigger a retry turns a deterministic-nonce reuse into full key-share recovery. The precondition is operational rather than cryptographic, making this a realistic medium-to-high likelihood in any deployment where attempts can be repeated.

### Recommendation
Delete the cached preprocess from the DB before (or atomically with) its use in `preprocess_internal` — e.g., call `CachedPreprocesses::del`-equivalent removal immediately after `CachedPreprocesses::get`, or make `share_internal` consume the cache so a second attempt either generates a fresh seed or fails closed. Also ensure `preprocess` round emission records the published commitments so a restarted node cannot re-derive and re-broadcast identical nonces under a different signing set.

### Proof of Concept
1. Validator A initiates signing under context `C`. `preprocess_internal` stores seed `s` (XORed with the encryption key) in `CachedPreprocesses` and returns machine/preprocess derived from `s`.
2. The first `share_internal` call runs `from_cache(algorithm, keys, s)`, `sign(preprocesses_1, msg)` producing share `s1` over nonces `d + e·ρ1`.
3. A retry (or a malicious peer inducing a second attempt with a different participant set/preprocess map) under the same `context` causes `share_internal` to call `preprocess_internal` again; `CachedPreprocesses::get` returns the still-present `s`, reconstructing identical nonces `d`, `e` (crypto/frost/src/sign.rs:391-394 applies `actual = base + rho * actual` to the same seeded `Nonce`s).
4. Second share `s2` is emitted with a different binding factor/challenge.
5. From `s1`, `s2`, the known `ρ`s and `c`s, the attacker solves for `λ·x` and then `x`, recovering validator A's secret share — violating the `from_cache` "must be deleted so it's never reused" contract at crypto/frost/src/sign.rs:218-219.