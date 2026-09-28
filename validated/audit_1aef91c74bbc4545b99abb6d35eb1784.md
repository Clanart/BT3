### Title
Cached FROST preprocess seed is reused across calls instead of being consumed, enabling nonce reuse and key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug class is "an object is referenced again after it should already be gone" — in `panthor_gem_create_with_handle()`, the GEM object was tracked/used after `drm_gem_object_put()` could have freed it. The structural analog in Serai is `SigningProtocol::preprocess_internal` / `share_internal`: the FROST preprocess seed stored in `CachedPreprocesses` is a one-time-use object that is never consumed. It is looked up and reused to deterministically regenerate the same secret nonces every time a function in the same `context` runs, so the nonce material is "used after" the point at which the protocol demands it be destroyed.

### Finding Description
`preprocess_internal` writes `CachedPreprocesses::set(txn, context, seed)` once and then, on every subsequent invocation with the same `context`, loads the same seed and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` (lines 123–145). `from_cache` feeds the seed into `ChaCha20Rng` and regenerates identical `nonces` and `commitments` in `seeded_preprocess` (`crypto/frost/src/sign.rs:121-144`).

The FROST spec in this repo is explicit that this object is single-use: "After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" (`crypto/frost/src/sign.rs:218-219`), and the FROST doc repeats that a reused seed leaks the private key share (`spec/cryptography/FROST.md:51-62`). Yet the seed is never deleted from `CachedPreprocesses` after use.

Concretely, in `SigningProtocol::share_internal` (line 156) the machine is rebuilt from the cached seed, and in `DkgConfirmer`, both `share()` (line 309) and `complete()` (line 321–324) call `share_internal` under the identical context `(b"DkgConfirmer", self.attempt)` (line 275). Each call signs with the *same* secret nonces `d, e`. The inputs that differentiate the two signings — the `preprocesses` map and the `key_pair` used to build `set_keys_message` (lines 294–300) — are deserialized from externally supplied bytes (`HashMap<Participant, Vec<u8>>` of preprocesses). Two signings under the same context with different preprocess sets produce different binding factors `b`, different `rho`, and a different challenge `c`, while `d` and `e` are identical. The share broadcast by `share()` is `z = d + e·b + λ·c·s`; a second share over the same nonces with a different `b`/`c` gives a linear system that recovers the secret share `s` — textbook FROST nonce reuse.

### Impact Explanation
High. Recovery of a coordinator validator's MuSig/FROST secret share for the DKG-confirmation key lets an attacker (combined with the public group key structure) forge confirmations or contribute to recovering the aggregate key. This is exactly the "key share recovery" acceptance criterion.

### Likelihood Explanation
The reuse is deterministic — it happens on every `share()`/`complete()` pair and on any repeated `share()` for the same attempt. Whether an attacker can control the differentiating inputs depends on how many times `DkgConfirmer::share`/`complete` are invoked per attempt with distinct validator-supplied preprocess sets (callers in `coordinator/src/tributary/handle.rs` were not fully traceable in this pass). If `share()` is ever callable twice for one attempt — e.g., a re-invocation after a changed preprocess set — two publicly published shares over identical nonces exist and recovery is immediate. Even absent a second public share, `complete()` unconditionally re-signs internally with reused nonces, so any single leaked share from a *different* signing under the same seed (e.g., a retry) is fatal.

### Recommendation
Delete the `CachedPreprocesses` entry when it is consumed (in `share_internal`, after `from_cache`, remove the key for `context`), so that the seed — like the freed GEM object — cannot be referenced after its single legitimate use. If the machine must be rebuilt for `complete()`, persist the `AlgorithmSignatureMachine` itself rather than re-deriving it from the consumed seed.

### Proof of Concept
1. For context `(b"DkgConfirmer", attempt)`, call `DkgConfirmer::share(preprocesses_A, key_pair)` → publishes share `z1 = d + e·b1 + λ·c1·s`.
2. Trigger a second signing under the same context with a different participant-supplied preprocess set `preprocesses_B` (different `b2`, `c2`) → `z2 = d + e·b2 + λ·c2·s`.
3. The two equations with known `b1, b2, c1, c2, λ` and identical `d, e` yield `s` by linear elimination, recovering the validator's secret key share.