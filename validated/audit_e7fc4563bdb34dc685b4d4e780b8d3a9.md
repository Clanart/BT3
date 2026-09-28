### Title
Cached FROST preprocess seed is never invalidated after signing, enabling nonce reuse and secret share recovery across repeated share/complete invocations - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to the NocoDB report — where a state change (password reset) invalidated JWTs but left refresh tokens usable — `SigningProtocol::preprocess_internal` caches a deterministic preprocess seed in the `CachedPreprocesses` DB keyed only by `context`, and never deletes or rotates it after the machine is consumed. Both `DkgConfirmer::share` and `DkgConfirmer::complete` independently call `share_internal` → `preprocess_internal`, so every signing pass under the same `(b"DkgConfirmer", attempt)` context deterministically regenerates identical FROST nonces via `ChaCha20Rng::from_seed(*seed.0)` in `seeded_preprocess`. If the signed message differs between invocations, the result is classic Schnorr/FROST nonce reuse.

### Finding Description
- `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:123-147) only generates a fresh seed when `CachedPreprocesses::get(txn, &context)` returns `None`; afterward the seed persists forever. There is no deletion path anywhere — the doc on `SignMachine::from_cache` (crypto/frost/src/sign.rs:216-219) states "the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share," yet no code performs that deletion.
- `share_internal` (line 156) calls `self.preprocess_internal(participants).0`, producing an `AlgorithmSignMachine` whose nonces are deterministically derived from the cached seed (crypto/frost/src/sign.rs:127-141), then signs `msg`.
- `DkgConfirmer::share` (lines 304-310) and `DkgConfirmer::complete` (lines 312-327) each invoke `share_internal`. `complete` re-signs the message even though `share` may have already been called earlier for the same attempt (line 322-324).
- The signed message is `set_keys_message(&self.spec.set(), &removed, key_pair)` (lines 296-300). `key_pair` is an argument supplied at call time, not bound into the cache key. The cache key is only `(b"DkgConfirmer", attempt)` (line 275) — so two different `key_pair` values under the same attempt produce two signature shares over **different messages with identical nonces**.
- With two shares `s₁ = d + b·ρ₁ + λ·x·e₁` and `s₂ = d + b·ρ₂ + λ·x·e₂` over distinct messages, an observer solves the linear system for `λ·x` — the interpolated secret share — exactly the "reuse will enable third-party recovery of your private key share" failure the spec warns about (spec/cryptography/FROST.md:51-53).

### Impact Explanation
Recovery of the validator's MuSig/FROST secret share (`self.key`, the tributary signing key share). Since shares and the final signature are broadcast, any party observing two shares bound to the same cached nonces but different `key_pair` messages recovers the share and can forge tributary signatures for that participant's position — direct key-share recovery, the most severe outcome class for this protocol.

### Likelihood Explanation
The trigger is two `share`/`complete` invocations under one attempt with differing `key_pair`, or any re-execution path (crash recovery, re-scan of tributary events, retry) that re-signs a mutated message before the attempt counter advances. Because the seed is persisted in the DB rather than held in memory, a node restart does not save it — `from_cache` faithfully regenerates the same nonces, making reuse more, not less, likely across retries. The missing `CachedPreprocesses` deletion is an unconditional code fact; the only question is whether the surrounding protocol ever varies `key_pair` within an attempt, which the key-pair-as-argument design does not prevent.

### Recommendation
Delete `CachedPreprocesses` for the context immediately after `share_internal` succeeds (first consumption), matching the "must be deleted" contract in `SignMachine::from_cache`. Alternatively, bind the message/`key_pair` hash into the cache key so a message change forces a fresh seed, and refuse to sign twice with the same seed.

### Proof of Concept
1. During DKG attempt `N`, the coordinator's `DkgConfirmer::share` is invoked with `key_pair_A`; `preprocess_internal` loads cached seed `S`, deriving nonces `(d, e)` and producing share `s₁` over `m₁ = set_keys_message(set, removed, key_pair_A)`.
2. `DkgConfirmer::complete` (or a second `share`) is invoked with `key_pair_B ≠ key_pair_A` in the same attempt; the cache still holds `S`, nonces `(d, e)` are regenerated identically, and share `s₂` over `m₂` is emitted.
3. An observer computes binding factors `ρ₁, ρ₂`, challenges `e₁, e₂` from public data, and solves `s₁ − s₂ = b(ρ₁ − ρ₂) + λx(e₁ − e₂)` together with the shared-nonce equations to recover `λx`, the participant's interpolated secret share — forgeable signatures follow.