### Title
Deterministic CachedPreprocess reuse across `share()`/`complete()` re-execution signs twice under identical FROST nonces, enabling validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists a single `CachedPreprocess` per `context` and rebuilds the FROST sign machine from it via `AlgorithmSignMachine::from_cache` on **every** invocation. `share_internal` calls `preprocess_internal` unconditionally (line 156), and `share_internal` is itself invoked once by `DkgConfirmer::share` (line 301) and again inside `DkgConfirmer::complete` (line 322). Nothing enforces that the two invocations see identical `(preprocesses, key_pair)` inputs — both are caller-supplied parameters to `complete`, and `DkgKeyPair::set` (handle.rs:54) blindly overwrites prior state. Any divergence produces two Schnorr signature shares generated with the same `(d, e)` nonces — the exact FROST nonce-reuse condition that leaks the signer's secret share.

### Finding Description
`preprocess_internal` derives `machine` via `from_cache(algorithm, keys, CachedPreprocess(cached))` using the DB-cached seed, so the hiding/binding nonces `(d, e)` are fixed for a given `context = (b"DkgConfirmer", attempt)` (lines 137–145, 275). In `crypto/frost/src/sign.rs` (lines 386–396), `sign` computes the effective nonce as `d + e·ρ` where `ρ` is the binding factor derived from the participant set's preprocesses, and the share is `s = d + e·ρ + c·λ·x` (`sign_share`, algorithm.rs:208–210).

- If `share()` and the internal re-sign inside `complete()` run with the **same preprocess set but a different `key_pair`** (hence different `msg` = `set_keys_message`), then `ρ` and `λ` are identical and only `c` changes: `s1 − s2 = λ·x·(c1 − c2)`, yielding `x` — the validator's MuSig private share — by field division.
- If the preprocess sets differ, `ρ` changes too, leaving two unknowns `(e, x)`; a third signing under the same context resolves them.

The module's own safety argument (lines 34–48) rests entirely on "all inputs are identical under BFT," yet `key_pair` is a local processor-supplied value, `complete` takes `preprocesses`/`key_pair` as fresh parameters rather than the DB-pinned values used by `share`, and the authors flag the missing commitment-consistency check as TODO (lines 50–54).

### Impact Explanation
Exposure of a validator's Ristretto MuSig secret share breaks the root-of-trust key used to confirm DKG results on Substrate (`set_keys_message`). In the n-of-n MuSig context, any coalition able to observe two divergent shares (e.g., co-signers extracting our share from the published aggregate, or two published shares) recovers the full validator private key, enabling forgery of validator signatures and compromise of key-set confirmation — analogous to the replication-channel takeover in the reference CVE, here via re-executed protocol state rather than a replication stream.

### Likelihood Explanation
Medium. Exploitation requires the coordinator to sign twice under one `attempt` with divergent inputs — reachable if `generated_key_pair`/`complete` are driven with differing `key_pair` or preprocess sets (retries, conflicting DKG results, or a rebuilt process with stale DB state). The code explicitly documents that correctness is not yet enforced (TODOs), so divergence is unguarded rather than impossible.

### Recommendation
Pin the signing inputs at preprocess time: store the exact `preprocesses` and `key_pair` (or their hash) alongside `CachedPreprocesses` when first created, and refuse to produce a share if `share`/`complete` are invoked with anything different — implementing the acknowledged TODO at lines 50–54. Alternatively, derive the nonce seed from `H(context || serialized_preprocesses || msg)` so any input change yields fresh nonces.

### Proof of Concept
1. Trigger `generated_key_pair`/`share` for attempt `a` with `key_pair_A` → share `s1` over `msg_A`, nonces `(d, e)`, binding factor `ρ`, challenge `c1`.
2. Cause `DkgConfirmer::complete` (or a second `share`) for the same attempt with `key_pair_B` → share `s2` over `msg_B`, same `(d, e, ρ)`, challenge `c2`.
3. `x = (s1 − s2) · (λ·(c1 − c2))^{-1}` recovers the validator's secret share; `G·x` equals the validator's public key, confirming recovery.