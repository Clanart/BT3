### Title
Failed/repeated DKG-confirmation signing reuses cached nonces, enabling private key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The reported bug class — a failed operation leaving dangerous authorization state in place (allowance never reset) — maps directly onto `SigningProtocol::preprocess_internal` / `share_internal`. A FROST preprocess *seed* is persisted in `CachedPreprocesses` keyed only by `(b"DkgConfirmer", attempt)`, and it is never deleted, rotated, or invalidated when signing fails or when signing is re-executed. Every call to `share_internal` (via `DkgConfirmer::share` and again inside `DkgConfirmer::complete`) calls `AlgorithmSignMachine::from_cache`, which deterministically regenerates the *same* nonces from the cached seed. The nonce is only "consumed" in-memory (`self.nonces.drain(..)` in `sign`), never in the DB — so the persistent state is never reset, exactly like the un-reset allowance. `coordinator/src/tributary/signing_protocol.rs:123-181`, `crypto/frost/src/sign.rs:268-274,388-398`.

### Finding Description
`preprocess_internal` writes the seed once if absent and re-reads it on every call:

- Seed cached under `context = (b"DkgConfirmer", attempt)`; never removed after `share` succeeds, fails, or `complete` runs (`signing_protocol.rs:123-147`).
- `share_internal` rebuilds a fresh machine from that seed each invocation (`signing_protocol.rs:156`).
- `DkgConfirmer::complete` calls `share_internal` *again* (`signing_protocol.rs:321-324`), regenerating identical nonces.
- The message signed is `set_keys_message(set, removed, key_pair)` where `key_pair` and `preprocesses` are supplied per-call (`signing_protocol.rs:288-301`) — they are not bound into the cached-preprocess context.

In FROST the produced share is `share_i = secret_share_term + nonce` where `nonce = base + rho * actual`, and `rho`/the challenge depend on the preprocesses and `msg` (`sign.rs:361-398`). If `sign` is executed twice under the same `attempt` context with different `preprocesses` or different `key_pair` — e.g., after `share()` returns `Err(InvalidParticipant)` on a malformed preprocess and is retried, or `complete()` is invoked with a different `key_pair`/preprocess set than `share()` — the same seed produces the same `base`/`actual` nonces while `rho` and the challenge differ. Two distinct shares on one nonce pair allow solving for the secret share by linear algebra, and the file's own header admits safety depends entirely on message determinism plus a check marked TODO (`signing_protocol.rs:25-54`, lines 50-51: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)").

This is the structural equivalent of the reported bug: a failure path (`Err` from `machine.sign` / `read_preprocess`) does not roll back the dangerous state — the reusable nonce seed persists in the DB and is handed to the next attempt, just as the spender allowance persisted to the next trade.

### Impact Explanation
An unprivileged participant can submit untrusted preprocess bytes (parsed via `read_preprocess`, `signing_protocol.rs:165`), forcing `share` to fail, then supply different preprocesses or trigger `complete` with a divergent `key_pair`/participant set. The coordinator then emits a second signature share computed over the same nonces under a different binding factor and challenge. From two such shares, the signer's long-term secret share (the validator's MuSig key share protecting the DKG confirmation root of trust) is recoverable — full key compromise, not merely a stuck trade.

### Likelihood Explanation
The unsafe state persists by construction: nothing deletes `CachedPreprocesses` after use or failure, and `share()`/`complete()` take `preprocesses` and `key_pair` as free parameters rather than values bound to the cached context (`signing_protocol.rs:288-327`). Reaching divergence requires a validator-set peer to supply differing preprocess bytes across the `share`/`complete` calls or a failed-then-retried `share` with changed inputs — attacker-controlled inputs within the allowed read_preprocess surface, though gated by the coordinator's BFT-ordered message flow. Residual uncertainty: whether tributary message ordering strictly pins `preprocesses`/`key_pair` between the two calls could not be fully verified within the search iterations; the missing commitment-binding check is self-acknowledged as a TODO.

### Recommendation
Delete (or mark consumed) `CachedPreprocesses[context]` before producing a share — i.e., burn the nonce seed on first `sign` execution and refuse to sign if it is absent — and bind the exact `preprocesses` digest and `msg` (key_pair) into the stored context so a retry with different inputs uses a different seed or errors. Implement the noted TODO: verify on-chain commitments match the presumed preprocess before publishing any share. This mirrors the report's fix of zeroing the allowance on failure: reset the dangerous capability when the operation does not cleanly complete.

### Proof of Concept
1. Context `(b"DkgConfirmer", attempt)` stores seed `s`; `share(preprocesses_A, key_pair_A)` → `share_internal` → `from_cache(s)` → nonces `n`; emits share `σ_A = f(secret, n, rho_A, msg_A)`. Suppose it instead first fails (`Err(InvalidParticipant)` on an attacker preprocess) — `s` remains in the DB.
2. Trigger `complete(preprocesses_B, key_pair_B, shares)` (or a retried `share` with changed inputs) → `share_internal` again → `from_cache(s)` regenerates identical `n`; emits `σ_B = f(secret, n, rho_B, msg_B)` with `rho_B/challenge_B ≠ rho_A/challenge_A`.
3. Solve the two linear share equations for the secret share, recovering the validator's private key share — analogous to repeatedly exploiting an un-reset allowance across failed trades.