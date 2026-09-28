The strongest analog candidate is the cached-preprocess reuse in `signing_protocol.rs`. Let me check how `context` is derived and whether the cache is ever invalidated.### Title
Cached FROST preprocess seed is reused across `share`/`complete` calls, enabling nonce reuse and key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to the configfs bug — where locking the parent directory protected entries but not the *cursor* position — `SigningProtocol::preprocess_internal` persists a `CachedPreprocess` seed in `CachedPreprocesses` keyed only by `context`, but never invalidates it after consumption. Each call to `preprocess_internal` (via `preprocess()`, `share_internal()`, and `complete()`) regenerates a deterministic `AlgorithmSignMachine` from the same seed via `AlgorithmMachine::seeded_preprocess` (`ChaCha20Rng::from_seed(*seed.0)`), producing identical FROST nonces. The FROST API contract explicitly requires the cached preprocess to be deleted after `from_cache` ("After this, the preprocess must be deleted so it's never reused"), yet the DB entry is only written once (`if ... .is_none()`) and is read back on every subsequent call. Any two `share`/`complete` invocations for the same `context` that sign differing data reuse the same nonces — a ROS-style nonce-reuse condition that leaks the signer's secret share.

### Finding Description
In `coordinator/src/tributary/signing_protocol.rs`, `preprocess_internal` stores a freshly generated seed XOR-encrypted into `CachedPreprocesses` under `self.context` the first time it runs, then on every later call loads the same seed and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`. `from_cache` rebuilds `nonces` deterministically from that seed (`crypto/frost/src/sign.rs`, `seeded_preprocess`), so the preprocess message — and critically the secret nonces `d, e` — are identical on every call sharing the same `context`.

`DkgConfirmer` sets `context = (b"DkgConfirmer", self.attempt)`, so the seed is stable for the entire DKG attempt. Three public entry points consume it:

- `preprocess()` → `preprocess_internal()` (reads seed, emits preprocess)
- `share()` → `share_internal()` → `preprocess_internal()` (reads seed **again**, signs `set_keys_message`)
- `complete()` → `share_internal()` → `preprocess_internal()` (reads seed **a third time**, signs again)

The bug class maps precisely: the DB key (the "locked parent") stays fixed while the seed (the "cursor") is dereferenced repeatedly without being consumed/removed — there is no `CachedPreprocesses::del` or equivalent invalidation anywhere in `share_internal`/`complete`. Because `share` and `complete` each accept independently supplied `preprocesses`/`shares`/`key_pair` maps, the signed `msg` and/or participant set can differ between invocations while the nonce commitment `R` stays fixed. Two shares `s₁ = k + c₁·λᵢ·xᵢ` and `s₂ = k + c₂·λᵢ·xᵢ` over the same `k` with different challenges yield `xᵢ ∝ (s₁ − s₂)/(c₁ − c₂)` — direct secret-share recovery.

### Impact Explanation
Reuse of FROST nonces across distinct signing sessions is the canonical key-recovery attack. Any party observing two signature shares (or two completed Schnorrkel signatures) produced under the same attempt context with differing preprocess sets, participant sets, or key material recovers the validator's MuSig secret share. With the threshold structure of `musig(...)`, recovered shares collapse the tributary's t-of-n key, enabling forgery of `set_keys_message` signatures — i.e., signing of an unintended validator-set confirmation. This is a documented-MUST violation ("the preprocess must be deleted so it's never reused" in `crypto/frost/src/sign.rs` and `spec/cryptography/FROST.md`), but the *caller's* failure to delete is a code defect, not integrator misuse.

### Likelihood Explanation
`complete()` unconditionally invokes `share_internal` a second time, guaranteeing at least two nonce derivations per attempt. Whether the two derivations sign byte-identical inputs depends on the caller-supplied `preprocesses` map and `key_pair`, which are provided independently at each call site (`handle.rs`). A crash-and-retry between `share` and `complete`, or any coordinator path that re-issues `share` with an updated preprocess set for the same `(b"DkgConfirmer", attempt)` context, produces nonce reuse. Reachability requires no malicious internal state — only that two invocations for the same attempt observe different inputs.

### Recommendation
Delete the `CachedPreprocesses` entry (or mark it consumed within the same DB transaction) immediately after the first `from_cache` in `preprocess_internal`, and have `share`/`complete` carry the already-materialized `AlgorithmSignMachine`/`AlgorithmSignatureMachine` forward instead of re-deriving it. At minimum, `complete` should reuse the machine produced by the corresponding `share` call rather than calling `share_internal` again.

### Proof of Concept
1. Trigger a DKG confirmation attempt `a` so `DkgConfirmer::new(..., a)` yields `context = (b"DkgConfirmer", a)`.
2. Call `preprocess()` → seed `S` stored; machine `M₁ = seeded_preprocess(S)` emits `Preprocess{commitments}` with nonces `(d, e)` and commitment `R`.
3. Call `share(P₁, kp₁)` → rebuilds `M₁` from `S` (identical `(d, e)`), produces share `s₁` for `msg₁ = set_keys_message(set, removed, kp₁)` under preprocess set `P₁`.
4. Call `complete(P₂, kp₂, shares)` with `P₂ ≠ P₁` or `kp₂ ≠ kp₁` → internally calls `share_internal` again, regenerating the same `(d, e)` and producing `s₂` over a different effective challenge `c₂` (the binding factor `ρ` over `group_key` and the per-participant commitments differs).
5. From `s₁`, `s₂`, and the public commitment `R = d·G + ρ·e·B`, solve for the secret share: `xᵢ = (s₁ − s₂) / (λᵢ·(c₁ − c₂))` — full share recovery from publicly broadcast shares.

Code paths: `coordinator/src/tributary/signing_protocol.rs` lines 123–147 (seed persisted, never deleted), line 301 (`share_internal` re-entry), lines 321–324 (`complete` re-derives the sign machine); `crypto/frost/src/sign.rs` lines 127–143 (`seeded_preprocess` determinism) and lines 216–224 (`from_cache` reuse warning).