### Title
Reused cached FROST nonce across divergent DKG-confirmation messages enables validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives the FROST preprocess from a seed stored in `CachedPreprocesses`, keyed only by `context = (b"DkgConfirmer", attempt)` (signing_protocol.rs:123-145). The seed is never invalidated or re-keyed on anything else. Both `share()` (via `generated_key_pair`) and `complete()` (inside `DkgConfirmed` handling) re-execute `share_internal` → `sign()` using that same cached seed, while the inputs that determine the signed message and the signing set — the `removed` validator list and the participant→MuSig-index mapping — are fetched fresh inside `DkgConfirmer::new` via `removed_as_of_dkg_attempt` at each invocation (signing_protocol.rs:271, handle.rs:535, signing_protocol.rs:294-301).

### Finding Description
The kernel analog is a double-cleanup that corrupts a "pending" sentinel so a resource is consumed twice under inconsistent state. Here the analog is the cached preprocess seed:

- `preprocess_internal` returns a deterministic `AlgorithmSignMachine` built from `ChaCha20Rng::from_seed(*seed)` (crypto/frost/src/sign.rs:127-133), so the same nonce scalars `d, e` are regenerated on every call for a given `(b"DkgConfirmer", attempt)` context.
- `generated_key_pair` calls `share(preprocesses, key_pair)` which signs `msg = set_keys_message(set, removed, key_pair)` (handle.rs:57-59, signing_protocol.rs:296-301). `removed` is embedded in the message.
- `complete` later calls `share_internal` again and unconditionally re-signs (signing_protocol.rs:321-324).

If the `removed` set returned by `removed_as_of_dkg_attempt` differs between those two executions — e.g., a `RemoveParticipantDueToDkg` slash lands between key-pair generation and the `DkgConfirmed` accumulation completing — then:

1. `threshold_i_map_to_keys_and_musig_i_map` produces a *different* MuSig participant mapping and different `included` set (signing_protocol.rs:214-249), changing the per-participant binding context.
2. `set_keys_message` produces a *different* message, since `removed` is serialized into it.
3. `sign()` is invoked twice over the same nonce scalar pair with two different challenges `c1, c2`. For Schnorr shares `s_j = d + e·ρ + λ·x·c_j`, if the included set is identical but the message differs, `x = (s1 - s2) / (λ·(c1 - c2))` directly recovers the MuSig secret share — which in this protocol is derived from the coordinator's validator private key `self.key` (the encryption/decryption material at signing_protocol.rs:107-113 confirms `self.key` is the long-term validator key).

Both published `DkgConfirmed` shares are on-chain data readable by any unprivileged observer, so recovery needs only public data.

### Impact Explanation
The leaked secret is the validator's root-of-trust private key used for MuSig `set_keys` confirmation and all tributary `Signed` transactions. Its recovery lets an attacker forge `Signed` transactions as that validator (slash reports, sign data, votes), which is equivalent to key share recovery — the exact consequence the code comments warn about ("recovery of it will enable recovering the private key", signing_protocol.rs:104).

### Likelihood Explanation
The bug requires `removed` to change for the same `attempt` between share issuance and completion. `removed_as_of_dkg_attempt` reads removal state from the DB at call time, and `RemoveParticipantDueToDkg` votes are processed in `handle_application_tx` throughout the attempt's lifetime (handle.rs:258-284), so the set is not provably frozen. Additionally, `generated_key_pair` unconditionally overwrites `DkgKeyPair` and re-signs whatever `key_pair` it is handed with no idempotency check (handle.rs:54-59): any second invocation for the same attempt with a divergent `key_pair` reuses the identical nonce over a different message. Because I could not confirm whether removal mid-attempt forces a new attempt number (which would change the context key and be safe), the reachability depends on that DB semantics; the missing guard itself is unambiguous.

### Recommendation
- Include the canonical `removed` set (and a digest of `key_pair`) in the `CachedPreprocesses` context key, or store the signed message alongside the seed and refuse to `sign()` a different message under a cached seed.
- Make `generated_key_pair` idempotent: if `DkgKeyPair::get` for `(genesis, attempt)` returns a different `key_pair` than previously signed, refuse to sign rather than emitting a second share.
- Delete/`burn` `CachedPreprocesses` entries once `complete` succeeds, and have `complete` reuse the signature machine produced by `share` instead of re-deriving a machine and re-signing via `share_internal`.

### Proof of Concept
1. Validator set runs DKG attempt `A`; `generated_key_pair` signs `set_keys_message(set, removed=R1, kp)` producing share `s1 = d + e·ρ + λ·x·c1`, published as `DkgConfirmed`.
2. Before `DkgConfirmed` accumulation reaches `n`, `RemoveParticipantDueToDkg` votes change the removal state so `removed_as_of_dkg_attempt` now returns `R2 ≠ R1`.
3. On completion, `complete` → `share_internal` regenerates the same `(d, e)` from `CachedPreprocesses[(b"DkgConfirmer", A)]` and signs `set_keys_message(set, R2, kp)` — different message, same nonce — yielding `s2`, also published.
4. Attacker reads both on-chain shares and solves `x = (s1 - s2)/(λ·(c1 - c2))` (with `λ` computable from the public participant set), recovering the coordinator's validator private key.

Caveat: full confirmation requires verifying `removed_as_of_dkg_attempt` can return different sets within a single attempt (index coverage didn't expose its body); if removal strictly bumps the attempt counter before either sign occurs, the context key changes and this is safe. The absent idempotency guard in `generated_key_pair` remains an independent nonce-reuse hazard for any duplicate/divergent `GeneratedKeyPair` emission under one attempt.