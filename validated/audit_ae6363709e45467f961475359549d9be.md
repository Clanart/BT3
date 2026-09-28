### Title
Cached FROST preprocess is never marked spent, allowing deterministic nonce reuse after failed/repeated signing - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` encrypts a FROST `CachedPreprocess` seed under `CachedPreprocesses` and rebuilds `AlgorithmSignMachine` from that same stored seed on every call. `crypto/frost` explicitly states a cached preprocess “MUST only be used once” and that reuse enables recovery of the private key share. The coordinator never deletes or marks the cached preprocess as consumed after `read_preprocess`, `sign`, or `complete` fails/returns, so an attacker-controlled preprocess/share stream can cause the same nonce seed to be loaded again for the same context.

### Finding Description
The bug-class analog is “failed update leaves object usable”: the ntfs3 bug warned instead of marking an inode bad after a failed rename. Here, the code treats the cached preprocess as reusable despite `SignMachine::from_cache` documentation requiring deletion after use. In `preprocess_internal`, if no cache exists it creates `(machine, _) = AlgorithmMachine::new(...).preprocess(&mut OsRng)`, XORs `machine.cache()` with an encryption key derived from `context || secret`, stores it via `CachedPreprocesses::set`, then always loads `CachedPreprocesses::get` and calls `AlgorithmSignMachine::from_cache`. There is no `remove`/spent flag after successful `sign` or after errors. `share_internal` returns `Err(participant)` for malformed preprocesses/signing-set failures, but the deterministic seed remains in the DB; a retry reconstructs identical `nonces` because `seeded_preprocess` uses `ChaCha20Rng::from_seed(*seed.0)` to derive `Commitments::new` and the sign nonces.

### Impact Explanation
If the same cached FROST nonce seed is used in two signing runs that differ in message, participant set, binding factors, or aggregated `R`, the two emitted signature shares are linear equations in the same effective nonce. For Schnorr-style shares `s1 = k + e1*x` and `s2 = k + e2*x`, the secret share contribution can be recovered as `x = (s1 - s2)/(e1 - e2)` (with the actual FROST binding/Lagrange algebra applied by the attacker). This is key-share recovery, which is within the accepted impacts.

### Likelihood Explanation
Reachable by an unprivileged participant who supplies bytes to `read_preprocess` / `read_share` and can influence the preprocess map or trigger an error path (`InvalidPreprocess`/`InvalidShare`). The dangerous state persists because `preprocess_internal` caches deterministically per `context`; `DkgConfirmer` uses context `(b"DkgConfirmer", attempt)`, so repeated share/complete paths for the same attempt reload the same seed rather than rotating or invalidating it.

### Recommendation
Make cached preprocess consumption atomic and explicit: delete/mark `CachedPreprocesses[context]` spent before reconstructing the machine, or bind a one-use nonce to the exact signing attempt and erase it on any `share_internal`/`complete_internal` error. Do not regenerate a sign machine from a stored seed once `sign` has been attempted; require a fresh RNG seed for every retry.

### Proof of Concept
1. Trigger `SigningProtocol::preprocess_internal` twice for the same `context` (e.g. DKG confirmation attempt): first stores and loads seed `S`; second observes `CachedPreprocesses::get` already set and again calls `AlgorithmSignMachine::from_cache(..., CachedPreprocess(S))`.
2. Because `seeded_preprocess` derives nonces via `ChaCha20Rng::from_seed(S)`, both runs produce identical `nonces` and identical own `Commitments`.
3. Have a malicious participant supply an invalid/malformed preprocess on run 1 so `share_internal` errors after loading `S` but before clearing it; then supply a different valid preprocess set/message on run 2.
4. Collect the two emitted shares for the same nonce and solve the Schnorr/FROST share linear system to recover the validator’s secret share.