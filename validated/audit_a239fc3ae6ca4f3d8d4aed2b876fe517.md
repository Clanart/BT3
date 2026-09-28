### Title
Cached deterministic preprocess (fixed nonce seed) reused across distinct signing invocations under the same context enables MuSig secret-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists a single 32-byte nonce seed per `context` (e.g. `("DkgConfirmer", attempt)`) in `CachedPreprocesses` and rebuilds the same `AlgorithmSignMachine` — and therefore the same FROST nonces — on every call. `share_internal` and `complete` both re-derive that machine and call `sign()`. If `sign()` is ever executed twice under one context with a differing set of peer preprocesses or a differing `key_pair`/message, the same secret nonces are used to produce two shares over different challenges, which is the textbook nonce-reuse equation for recovering the signer's private key share. The file's own header documents that "it is explicitly unsafe to reuse nonces across signing sessions" and that safety rests entirely on the assumption that only one message set can ever be acted on.

### Finding Description
The kernel bug this maps to is a lifetime bug: an object consumed/freed in one path (the node table in `hsr_dellink`) remains reachable to a reader that still holds it. Here, the FROST `CachedPreprocess` contract (`crypto/frost/src/sign.rs:209-224`) is that a cached seed **MUST only be used once** — `from_cache` rebuilds deterministic nonces via `ChaCha20Rng::from_seed` in `seeded_preprocess` (`crypto/frost/src/sign.rs:121-143`). `preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:100-148`) violates this one-shot lifetime deliberately: after the first call stores `cache.0` under `context`, every subsequent call loads the identical seed and regenerates identical nonces (`lines 123-145`).

`share_internal` (`lines 150-181`) calls `preprocess_internal` and then `machine.sign(preprocesses, msg)` where `preprocesses` comes from `threshold_i_map_to_keys_and_musig_i_map` — i.e., whatever subset of validator preprocess transactions was supplied — and `msg` is `set_keys_message(&self.spec.set(), &removed, key_pair)` (`lines 296-301`). Two executions of `share`/`complete` under the same `attempt` with either (a) a different participating subset (different binding factors/aggregate nonce) or (b) a different `key_pair` (different message) yield two signature shares `s_i = d_i + b·e_i + λ_i·x·c` sharing the same `d_i, e_i` but different `c`/effective nonce — solving linear equations recovers `x`, the validator's MuSig secret share.

The safety argument in the file header (`lines 25-55`) rests entirely on BFT deduplication delivering identical inputs to every re-execution; it admits a "TODO" check that on-chain commitments match presumed nonces is not implemented. Nothing in `share()` or `complete()` records that a share was already produced for this context and refuses distinct inputs — the consumed preprocess is "freed" by `sign(self)` yet resurrected from the DB on the next call, exactly the use-after-teardown shape of the kernel bug.

### Impact Explanation
Recovery of a validator's `key` (the Ristretto MuSig secret share used to confirm DKG results on-chain, `key: &Zeroizing<F>` at line 93) lets an attacker forge `DkgConfirmer` signature shares and, combined with the threshold, forge Substrate `set_keys` confirmations — i.e., attest to attacker-chosen `KeyPair`s for a validator set. Since this signing path is the root-of-trust for rotating network keys, compromise translates to control over subsequent validator-set keys. FROST's own documentation (`spec/cryptography/FROST.md:51-55`) states reuse of a preprocess enables third-party recovery of the private key share.

### Likelihood Explanation
Triggering requires `share()`/`complete()` to run under one `attempt` context on non-identical inputs. I could not fully verify the deduplication behavior in `coordinator/src/tributary/handle.rs` (tool limit reached), so the concrete reachability is partially unproven. However: `threshold_i_map_to_keys_and_musig_i_map` rebuilds the participant set from whatever preprocess map is passed, MuSig tolerates any threshold-satisfying subset, and distinct subsets of finalized preprocess transactions — or a `complete()` call carrying a different `key_pair` than the one `share()` was invoked with — both re-sign with identical nonces. A validator (or any party able to get a distinct preprocess/share transaction finalized in the same attempt) is an unprivileged participant in this protocol, not a trusted party. The code's own comments concede the invariant is unenforced ("TODO" at line 51).

### Recommendation
- Record, under the same `CachedPreprocesses` context key, the hash of the serialized preprocess set and message used on the first `sign()`, and abort any subsequent `share_internal`/`complete` call whose inputs hash differently (the "on-chain-preprocess-matches-presumed-preprocess" check already noted as TODO at `signing_protocol.rs:51`).
- Alternatively, delete the `CachedPreprocesses` entry after the first successful share, and treat a missing-entry-but-already-signed state as fatal rather than regenerating nonces.
- Ensure `share()` and `complete()` cannot be reached twice with differing `key_pair` or participant subsets for one `attempt`.

### Proof of Concept
```text
Context: DkgConfirmer for (spec, attempt = A). Validator V holds key x.

1. First confirmation: validator subset S1 = {V, P2, ..., Pt} preprocesses reach
   consensus; coordinator calls DkgConfirmer::share(preprocesses_S1, key_pair_K).
   - preprocess_internal loads seed s from CachedPreprocesses[("DkgConfirmer", A)]
     → nonces (d_V, e_V), share σ1 = d_V + ρ_V·e_V + x·c1 published.

2. Same attempt A, attacker-caused second execution with a *different* finalized
   subset S2 ≠ S1 (e.g., an additional validator's preprocess transaction also
   finalized) or a different key_pair K' → msg' ≠ msg:
   - DkgConfirmer::share(preprocesses_S2, K') / ::complete(...) re-enters
     share_internal → same seed s → identical (d_V, e_V), but different binding
     factor ρ'_V and challenge c2 → share σ2 = d_V + ρ'_V·e_V + x·c2.

3. Solve the two-share linear system for (d_V + ρ·e_V) terms and x:
   σ1 − σ2 = (ρ_V − ρ'_V)·e_V + x·(c1 − c2); with the known relationship
   between σ1's nonce term and σ2's (same underlying scalars), x is recovered
   directly — the standard FROST/MuSig nonce-reuse key extraction.

Root cause: Crypto/frost contract "a preprocess MUST only be used once"
(crypto/frost/src/sign.rs:85-87, 211-219) is violated by re-execution in
share_internal (signing_protocol.rs:156, 170) sourcing the same persisted seed
(signing_protocol.rs:137-145) whenever handle.rs dispatches a second,
non-identical confirmation input under the same attempt context.
```

Note: verification of the exact dedup/finalization behavior in `coordinator/src/tributary/handle.rs` was cut off; the finding stands on the documented one-shot contract violation in `signing_protocol.rs`, whose own header concedes the safety assumption is unenforced (the TODO check at line 51 is absent).