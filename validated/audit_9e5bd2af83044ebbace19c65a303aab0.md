### Title
Cached FROST preprocess seed reused across distinct signing contexts enables secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches a 32-byte RNG seed under `(b"DkgConfirmer", attempt)` and reloads it via `AlgorithmSignMachine::from_cache` on every subsequent call. Because both `DkgConfirmer::share` and `DkgConfirmer::complete` route through `share_internal` (which calls `preprocess_internal` again), the same deterministic nonces are used to produce multiple signature shares whenever the message or participant set differs between calls — the exact nonce-reuse condition the FROST docs say enables third-party recovery of the private key share. This mirrors the advisory's class: a stale cached artifact keeps being used after the surrounding context (the `removed` validator set / `key_pair` being confirmed) has changed.

### Finding Description
In `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:100-148), a `CachedPreprocess` seed is generated once per `context` and persisted in `CachedPreprocesses`. Every later call with the same context skips regeneration (line 123 `is_none()` check) and rebuilds the sign machine from the identical seed via `from_cache` (lines 137-145). In `crypto/frost/src/sign.rs:121-144`, `seeded_preprocess` seeds `ChaCha20Rng::from_seed(*seed.0)`, so identical seeds yield identical nonces (`Commitments::new`) — and FROST.md lines 51-55 explicitly state reusing a preprocess "would enable a third-party to recover your private key share".

The reuse path: `DkgConfirmer::share` (line 304) and `DkgConfirmer::complete` (line 312) each call `share_internal`, which calls `preprocess_internal` → same seed → same nonces. However `share_internal` rebuilds `ThresholdKeys` via `musig(...)` over `participants` derived from `spec.validators()` filtered by `self.removed` — where `removed = removed_as_of_dkg_attempt(txn, genesis, attempt)` (line 271) — and builds `msg` from `set_keys_message(set, removed, key_pair)` (lines 296-300). Two invocations with different `removed` sets or different `key_pair`s produce different binding factors/challenges under the same nonce, leaking `s_i` via `share = nonce + c·λ_i·s_i`: given two shares `sh1 - sh2 = (c1 - c2)·λ·s_i`, the secret share is recovered.

### Impact Explanation
An unprivileged participant who supplies preprocess maps (network-delivered `HashMap<Participant, Vec<u8>>`) can induce a victim validator to emit two Schnorrkel signature shares over the same nonce with different challenges — e.g., a `share()` for one `key_pair` and a `complete()` for another, or across a mid-attempt change to the removed-validator set that alters the MuSig participant mapping (`threshold_i_map_to_keys_and_musig_i_map`, lines 241-245 re-indexes participants, changing λ and the aggregate key). Recovering a validator's MuSig secret share compromises the DKG-confirmation signature key and, combined with threshold-many recovered shares, the set key.

### Likelihood Explanation
The cache is keyed only on `(b"DkgConfirmer", attempt)` — not on `removed`, `participants`, or `key_pair`. Any divergence in those inputs between the cached generation and a later `share`/`complete` triggers nonce reuse. `share` then `complete` is the normal protocol flow (complete calls `share_internal` again at line 322), so two shares under one seed is reachable by design whenever the inputs differ; an attacker controlling a peer's preprocess submissions can force differing challenge transcripts.

### Recommendation
Key `CachedPreprocesses` on the full signing context — `attempt` plus `removed` set and `key_pair`/message hash — or delete the cached seed after first `from_cache` use (write-then-burn semantics), so a second `share_internal` for the same context generates a fresh seed or fails rather than reusing nonces.

### Proof of Concept
1. DKG attempt `a` begins; validator V calls `DkgConfirmer::preprocess()` → seed `s` cached under `("DkgConfirmer", a)`, preprocess `P` broadcast.
2. V calls `share(preprocesses_A, key_pair_1)` → `share_internal` reloads `s`, nonces `(d, e)` re-derived, emits `sh_1 = d + ρ_A·e + c_1·λ·s_v`.
3. Attacker (a peer participant) submits a different preprocess set/`key_pair_2`; V calls `complete(preprocesses_B, key_pair_2, shares)` → `share_internal` again uses `s`, emits `sh_2 = d + ρ_B·e + c_2·λ'·s_v`.
4. With known commitments in `P`, attacker solves the two-share linear system for `d, e` (or directly `s_v` when `λ` is unchanged), recovering V's private key share — the documented consequence of preprocess reuse in `crypto/frost/src/sign.rs:85-87`.