### Title
Deterministic CachedPreprocess reuse across signing attempts leaks the FROST secret share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator's `SigningProtocol` derives each signing machine's nonces from a preprocess seed that is (a) deterministically generated the first time, (b) stored in `CachedPreprocesses` keyed solely by `self.context`, and (c) never deleted or rotated after use. `share_internal` calls `preprocess_internal` for every `Shares` round under the same `context`, so every signing attempt for that context runs `AlgorithmSignMachine::from_cache` on the identical 32-byte seed and therefore commits to the identical FROST nonces. Any second signature produced under the same context reuses those nonces, which the FROST specification and Serai's own docs state is equivalent to handing out the secret share. This is the direct analog of a backdoored component silently exfiltrating key material: a routine, attacker-influenceable code path causes irreversible secret-key disclosure.

### Finding Description
`preprocess_internal` encrypts the machine cache with a key derived from `"Cached Preprocess Encryption Key" || context.encode() || key` and stores it under `CachedPreprocesses::set(txn, &self.context, ...)` (coordinator/src/tributary/signing_protocol.rs:107-135). On subsequent calls it loads the same entry and XOR-decrypts it back into `cached`, then constructs the sign machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` (lines 137-147). Nothing removes the entry after a successful or failed signing attempt.

`share_internal` (lines 150-181) invokes `self.preprocess_internal(participants)` unconditionally each time it runs, so each `Transaction::DkgShares`-style Shares round for the same `context` reuses the same seed.

Inside FROST, `seeded_preprocess` feeds that seed into `ChaCha20Rng::from_seed` and derives both the nonce scalars (`Commitments::new`, producing `d`, `e`) and the addendum deterministically (crypto/frost/src/sign.rs:121-144). Because `from_cache` routes back through `seeded_preprocess` (crypto/frost/src/sign.rs:268-274), identical seed ⇒ identical `(d, e)` and identical published preprocess commitments.

When `sign` executes, the effective nonce is `d + e * rho` where `rho` is bound to `(group_key, hash_msg(msg), preprocesses)` (crypto/frost/src/sign.rs:361-371, crypto/frost/src/nonce.rs:180-212). The emitted share is `s_i = (d + e*rho) + lambda_i * secret_share * c`. If two executions under the same context sign distinct messages `m1`, `m2` (or even the same message with a different signer set / different `rho`), the nonce term is reused while the challenge differs, so:

```
s_i1 - s_i2 = lambda_i * secret_share * (c1 - c2)
secret_share = (s_i1 - s_i2) / (lambda_i * (c1 - c2))
```

Both shares are broadcast publicly, so any observer recovers the participant's threshold secret share — exactly the failure mode Serai's own docs warn about: "Reusing preprocesses would enable a third-party to recover your private key share" (spec/cryptography/FROST.md) and the `CachedPreprocess` doc: "Any reuse will presumably cause the signer to leak their secret share" (crypto/frost/src/sign.rs:216-219).

### Impact Explanation
Full recovery of a validator's FROST secret share from publicly broadcast signature shares. Repeating across contexts/messages lets an attacker collect enough shares to reconstruct the group key's private key for the tributary validator set, enabling forgery of threshold signatures (Schnorrkel-over-Ristretto signatures authorizing Substrate/batch operations) — i.e., concrete signing of unintended messages and key-share recovery. The encryption of the cached seed only protects DB-at-rest; it does nothing to prevent deterministic reuse.

### Likelihood Explanation
Any unprivileged party who can cause a second signing round under the same `context` (e.g., a retried/resubmitted sign attempt, a competing transaction plan sharing the context key, or a second `Shares` collection after a failed `complete`) triggers reuse of identical nonces. The shares are published in `Transaction` payloads, so the attacker only needs to observe them — no validator collusion or privileged access is required. The exposure is systematic: it does not depend on RNG failure or malformed input, only on the coordinator re-entering `share_internal` for a context that was already signed.

### Recommendation
Delete the `CachedPreprocesses` entry (or mark it consumed) the moment `from_cache` consumes it, and/or key the cache by a per-attempt nonce (e.g., `context || attempt || participants`) so no two signing executions can regenerate the same seed. Additionally, make `seeded_preprocess`/`from_cache` defensive — e.g., bind the derived seed to the message/participant set or fail if called twice for the same cache — so reuse is impossible even if the DB entry lingers.

### Proof of Concept
1. Context C already has a stored `CachedPreprocesses` entry, or triggers `preprocess_internal` once to create it; the seed S is fixed.
2. Round 1: `share_internal(participants_P, msg = m1)` → `from_cache(S)` → nonces `(d, e)` → broadcast share `s_i1 = d + e*rho1 + lambda_i * x_i * c1` where `rho1` binds `hash_msg(m1)` and the signer set.
3. Round 2 under the same C (retry with different `msg` or a different participant subset): `share_internal(participants_Q, msg = m2)` → `from_cache(S)` regenerates the same `(d, e)` → share `s_i2 = d + e*rho2 + lambda'_i * x_i * c2` (for Lagrange interpolation `lambda_i` is publicly computable from `included`; crypto/dkg/src/lib.rs:226-249).
4. Solve for `x_i`: in the simplest case where `rho` is identical (same signer set), `x_i = (s_i1 - s_i2) / (lambda_i * (c1 - c2))`; with differing `rho`, `e` is still recoverable since `d, e` are reused — two equations in two unknowns `(d, e)` after subtracting the known `lambda*x*c` structure yields `x_i` directly.
5. Collecting `t` distinct shares across the set recovers the group private key, enabling arbitrary threshold-signature forgery.

Caveat: exploitability hinges on the coordinator permitting a second `share_internal` under an identical `context` value; the code unconditionally reloads the cache rather than consuming it, and `share_internal` itself is invoked per Shares round from tributary transaction handling, so any repeated signing for a shared context key reaches this path. I was unable to fully enumerate every `context` producer to confirm which contexts can legitimately repeat; the unsafe reuse behavior in `preprocess_internal`/`share_internal` is unconditional regardless.