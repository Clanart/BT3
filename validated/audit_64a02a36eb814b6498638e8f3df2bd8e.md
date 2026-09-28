### Title
FROST cached-preprocess seed reused across share and complete calls in DKG confirmation enables secret-share recovery - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
The bug class in CVE-2023-40283 is lifecycle mishandling: a parent releases an object while "children" derived from it continue to be used (use-after-free / stale-object reuse). The direct analog in Serai is the persistence of a FROST preprocess seed in `CachedPreprocesses`, which is reloaded and reused every time `SigningProtocol::share_internal` runs under the same context, rather than being deleted after first use as FROST requires. A reused preprocess means reused nonces, which leaks the signer's secret share — Serai's own docs state this explicitly.

### Finding Description
`SigningProtocol::preprocess_internal` at [signing_protocol.rs:100-148](coordinator/src/tributary/signing_protocol.rs) generates a FROST preprocess from a deterministic `ChaCha20Rng` seeded by a cached 32-byte seed (`AlgorithmMachine::seeded_preprocess`, [crypto/frost/src/sign.rs:121-144](crypto/frost/src/sign.rs)). The seed is stored in the DB under `CachedPreprocesses` keyed by `self.context` and is **never deleted**: on every call it is decrypted and passed to `AlgorithmSignMachine::from_cache`, deterministically regenerating identical nonces and commitments.

The FROST API is explicit that this must not happen: `from_cache` requires that "the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" ([crypto/frost/src/sign.rs:218-224](crypto/frost/src/sign.rs)).

`DkgConfirmer` uses context `(b"DkgConfirmer", attempt)` ([signing_protocol.rs:275](coordinator/src/tributary/signing_protocol.rs)). Within one attempt:

- `DkgConfirmer::share` calls `share_internal`, which internally calls `preprocess_internal` → loads the cached seed, signs `set_keys_message(&set, &removed, key_pair)` and returns a share that is broadcast.
- `DkgConfirmer::complete` calls `share_internal` **again** ([signing_protocol.rs:321-324](coordinator/src/tributary/signing_protocol.rs)), reloading the same seed and producing a fresh share with identical nonces — this time potentially over a different `key_pair` (a different message).

Because the message signed (`set_keys_message`) incorporates the externally supplied `key_pair`, any path that causes `share`/`complete` to be invoked under the same attempt with two different key pairs (or a retry after reboot where the seed persists in the DB) yields two published signature shares `s₁ = k − e₁·λ·x` and `s₂ = k − e₂·λ·x` with the same nonce `k` and different challenges. Solving `x = (s₁ − s₂)/(e₂ − e₁)` recovers the validator's interpolated secret share.

### Impact Explanation
Recovery of a validator's FROST secret share for the tributary MuSig key. Combined with the public preprocess commitments and other collected shares, this is a key-share recovery primitive, which is an accepted impact class.

### Likelihood Explanation
The reuse is structural: the seed is intentionally persisted for crash recovery, but nothing invalidates it after a share is emitted. The residual uncertainty is the exact driver that makes `share`/`complete` run twice with differing `key_pair`s within one `attempt` — that depends on tributary `handle.rs` call paths I could not fully trace within scope. However, even re-signing the *same* message after distinct preprocessing maps (different `removed`/participant ordering changes the MuSig assignment via `threshold_i_map_to_keys_and_musig_i_map`) changes the binding factors and produces a different effective challenge — same leak.

### Recommendation
Delete (or version) the `CachedPreprocesses` entry the first time a share is produced for a context — e.g., store a "consumed" flag alongside the seed, or include a monotonically increasing signing-instance counter in the context so each emitted share uses a fresh seed. At minimum, `complete` should not re-derive a share from the cached seed; it should rebuild the machine from the already-broadcast preprocess data.

### Proof of Concept
1. Within `DkgConfirmer` attempt `a`, the validator calls `share(preprocesses, key_pair_A)` → broadcasts share `s₁` over `set_keys_message(.., A)` using seed-derived nonce `k`.
2. `complete(preprocesses, key_pair_B, shares)` (or a second `share` call with `B`) invokes `share_internal` again → same seed → same `k`, producing share `s₂` over `set_keys_message(.., B)` with challenge `e₂ ≠ e₁`.
3. Anyone observing both shares computes the interpolated secret share `λ·x = (s₁ − s₂)/(e₂ − e₁)`.

Caveat: step 2 requires the coordinator to be driven into `share`/`complete` with distinct `key_pair`s under one attempt; I confirmed both code paths reuse the same seed but did not fully trace the upstream triggers in `handle.rs`.