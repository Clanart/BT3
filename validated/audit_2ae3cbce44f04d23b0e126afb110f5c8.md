### Title
Deterministic nonce regeneration via `from_cache`/`seeded_preprocess` enables secret share recovery when a cached preprocess is reused — (File: crypto/frost/src/sign.rs)

### Summary
CVE-2022-29178 is a privilege-escalation where a resource (Cilium's per-node API socket) that should have been restricted to a privileged owner (GID 0) was instead accessible to a broad, unprivileged default group (GID 1000): the security boundary failed because the wrong principal was implicitly trusted. The Serai analog is a nonce that should be exclusive to a single signing session being reproducible by anyone holding the cached seed: `AlgorithmMachine::from_cache` deterministically regenerates identical nonces with no freshness guard, so reuse of a `CachedPreprocess` across two signing sessions — reachable whenever an unprivileged party can cause two messages to be signed under the same cached context — yields the signer's secret key share.

### Finding Description
`AlgorithmSignMachine::from_cache` (crypto/frost/src/sign.rs:268-274) rebuilds a sign machine by calling `seeded_preprocess`, which seeds a `ChaCha20Rng` purely from the 32-byte cache and regenerates the nonces via `Commitments::new` (crypto/frost/src/sign.rs:121-144). The nonce scalars come from `C::random_nonce(secret_share, rng)` (crypto/frost/src/nonce.rs:58-61), which is deterministic given the same `(secret_share, seed)` — no binding to the message, participant set, or any session-specific data exists.

Concretely, `preprocess_internal` in the coordinator (coordinator/src/tributary/signing_protocol.rs:123-147) stores the encrypted `CachedPreprocess` under `self.context` and calls `from_cache` on **every** `share_internal` invocation for that context. Nothing deletes or rotates the entry after `sign()` consumes it; a retry, restart, or second `sign()` call under the same context reproduces the identical nonce pair `(d, e)` and identical preprocess commitments `(D, E)`.

In `sign()` (crypto/frost/src/sign.rs:283-411), the effective nonce is `d + rho * e` where `rho` is the per-participant binding factor derived from a transcript over `group_key`, `hash_msg(msg)`, and the preprocesses challenge (lines 361-379). Two `sign()` calls over different `msg` (or different signing sets) produce shares `s_i = d + rho_i*e + c_i*sigma` with distinct `rho_i` and challenges `c_i`. With repeated reuse, the attacker obtains a linear system over the unknowns `d`, `e`, and the interpolated share `sigma = l_i * secret_share` (+ offset), solvable once enough equations are collected — direct recovery of the FROST secret share, i.e., escalation from "can influence signed messages" to "holds the validator's private key share."

The header comments on `CachedPreprocess` (sign.rs:83-92) and `from_cache` (sign.rs:209-224) document the reuse hazard as a caller MUST, but the API provides no defense: no one-shot marker, no session binding, no mixed-in entropy. The code even makes reuse easy — `share_internal` re-enters `preprocess_internal` on every call rather than consuming the cache.

### Impact Explanation
Recovery of a Ristretto MuSig/FROST secret share for a tributary validator. Combined with shares recovered from other validators, this compromises the threshold key guarding cross-chain funds — an unprivileged party able to cause repeated signing under one cached context escalates to key material, matching the "key share recovery" acceptance criterion.

### Likelihood Explanation
Reachability requires the embedding protocol to invoke `sign()` twice under the same `CachedPreprocess` context with differing `msg` or differing preprocess sets. The coordinator design (persisting the cache keyed by context and reloading it per `share_internal` call rather than deleting it on first use) makes this a realistic failure mode on retries/restarts rather than pure misuse. An attacker does not need to be a validator; they need only influence which messages get signed in a reused context.

### Recommendation
1. Consume-and-delete: after `from_cache` materializes nonces, the persisted cache entry must be removed before `sign()` proceeds, so a crash cannot replay it.
2. Bind the seed: mix `msg`, the sorted `included` set, and a monotonically increasing counter into the ChaCha seed so regenerated nonces differ per session.
3. Add a defense-in-depth nonce hedge in `C::random_nonce` callers: XOR/extend the deterministic stream with fresh `OsRng` entropy where a fresh preprocess is acceptable.

### Proof of Concept
```rust
// crypto/frost/src/sign.rs — conceptual
// Same params (keys, algorithm) and same CachedPreprocess seed:
let seed = CachedPreprocess(Zeroizing::new([/* persisted bytes */]));
let (m1, _p1) = AlgorithmSignMachine::from_cache(algo.clone(), keys.clone(), seed.clone());
let (m2, _p2) = AlgorithmSignMachine::from_cache(algo.clone(), keys.clone(), seed);
// m1.nonces == m2.nonces (deterministic ChaCha20Rng from identical seed)
// sign() with msg_a != msg_b:
//   s_a = d + rho_a*e + c_a*sigma
//   s_b = d + rho_b*e + c_b*sigma
// Collecting >=3 such shares solves for (d, e, sigma) => secret share recovered.
```
The same pattern is exercised in production by `share_internal` re-reading `CachedPreprocesses` from the DB per call (coordinator/src/tributary/signing_protocol.rs:137-147).