### Title
Deterministic `CachedPreprocess` reuse across distinct signing sets enables FROST secret-share recovery - (File: crypto/frost/src/sign.rs)

### Summary

The external report's bug class is *state saved lazily / stale state silently reused instead of being flushed at handoff*. In Serai's FROST implementation, a `CachedPreprocess` is a 32-byte seed from which the entire preprocess — including both nonce scalars `(d, e)` — is deterministically re-derived via `ChaCha20Rng::from_seed(*seed.0)` in `AlgorithmMachine::seeded_preprocess` (`crypto/frost/src/sign.rs:121-144`). `SignMachine::from_cache` (`crypto/frost/src/sign.rs:268-274`) places no guard against loading the same seed twice, and `AlgorithmSignMachine::sign` mixes the nonces with per-participant binding factors `rho` that depend on the submitted preprocess set (`crypto/frost/src/sign.rs:283-396`, `crypto/frost/src/nonce.rs:161-191`). Two `sign` executions sharing a seed but differing in the preprocess map or included set produce two shares `s_i = d + e·rho_i + λ·c·x_i` with identical `(d, e)`, allowing an unprivileged co-signer to recover the victim's secret share `x_i`. The consumer (`coordinator/src/tributary/signing_protocol.rs:99-181`) caches the seed under only a coarse `context` key and re-invokes `preprocess_internal` inside `share_internal`, which is called by both `DkgConfirmer::share` and `DkgConfirmer::complete` (`coordinator/src/tributary/signing_protocol.rs:288-327`) — two distinct sign executions over attacker-influenced preprocess maps from a single cached seed.

### Finding Description

`seeded_preprocess` generates nonces solely as a function of the cached seed and the secret share:

- `crypto/frost/src/sign.rs:127-132`: `ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new(rng, secret_share, nonces)`.
- `crypto/frost/src/nonce.rs:53-72`: each `Nonce` is `C::random_nonce(secret_share, rng)` twice — deterministic given the seed.
- `crypto/frost/src/sign.rs:386-396`: the actual per-nonce scalar used in the share is `d + e·rho_n`, where `rho_n = C::hash_binding_factor(rho_transcript)` is computed in `BindingFactor::calculate_binding_factors` (`crypto/frost/src/nonce.rs:161-173`) from a transcript binding every participant's commitments (`crypto/frost/src/sign.rs:325-379`).

Because `sign` takes `preprocesses: HashMap<Participant, Preprocess>` as untrusted bytes via `read_preprocess` (`crypto/frost/src/sign.rs:276-281`), the rho values — and hence the *effective* nonce — vary with the attacker's preprocess set, while the raw `(d, e)` remain fixed for a reused seed. `complete` additionally accepts a fresh attacker-controlled `shares`/`preprocesses` combination on the same cached machine path in callers like `DkgConfirmer::complete`, which internally calls `share_internal` → `preprocess_internal` → `from_cache` with the same seed (`coordinator/src/tributary/signing_protocol.rs:137-145, 288-327`).

The cache key in `SigningProtocolDb::CachedPreprocesses` is only `context` = `(b"DkgConfirmer", attempt)` (`coordinator/src/tributary/signing_protocol.rs:86-90, 274-277`); it is not bound to the participant set or message, so any second signing pass under the same context silently reuses the seed — exactly the "lazy save without flush" pattern from the advisory.

### Impact Explanation

High. Given two shares `s1 = d + e·rho1 + λ·c·x` and `s2 = d + e·rho2 + λ·c·x` over the same message and included set (same `λ`, `c`) but different preprocess commitments (different `rho`), an attacker computes `e = (s1 − s2)/(rho1 − rho2)`, then `d`, then `x = (s1 − d − e·rho1)/(λ·c)` — full recovery of the victim's threshold secret share. Combined with `t−1` other shares or repeated across participants, this collapses the multisig's security and permits forging signatures for the group key (e.g., Bitcoin `TransactionSignMachine` flows using the same `AlgorithmSignMachine`/`Commitments` machinery in `networks/bitcoin/src/wallet/send.rs:321-398`).

### Likelihood Explanation

The library-side precondition is trivially satisfied: `from_cache` + `sign` is a public API reachable with untrusted preprocess bytes (`read_preprocess` on attacker-controlled input). The realistic trigger is the coordinator's own usage pattern, which rebuilds the sign machine from the cached seed once per `share`/`complete` call rather than consuming it once; any peer who can cause a second signing pass under the same `context` with a mutated preprocess map obtains the two needed shares. It requires influencing which preprocesses are submitted between two passes — achievable by a signing participant supplying different preprocess bytes in the two rounds — but requires the victim to execute two passes over the same context, which the existing code structure does.

### Recommendation

Bind the cached preprocess to the signing session it will be used for, and enforce single-use eagerly (mirroring the advisory's "eagerly save and flush" fix):

1. In `SigningProtocol::preprocess_internal`, delete (`remove`) the `CachedPreprocesses` entry after reading it in `share_internal`/`complete` flows, or key the cache by a hash of (context, included participants, message) so a stale seed can never be loaded for a different signing set.
2. In `crypto/frost`, make `AlgorithmSignMachine::sign` mix the seed-derived `blame_entropy`/session transcript into the nonce derivation, or store a monotonically incremented counter inside `CachedPreprocess`, so reloading the same seed cannot reproduce identical `(d, e)`.
3. Ensure `DkgConfirmer::complete` does not re-derive a share through `share_internal` on the same seed after `share` already consumed it; carry the produced `AlgorithmSignatureMachine` forward instead of reconstructing it.

### Proof of Concept

```rust
// crypto/frost — conceptual PoC over the in-scope API
let keys: ThresholdKeys<C> = victim_keys.clone();
let seed = victim_cached_seed; // the single cached [u8; 32] for context C

// Pass 1: attacker preprocess set A
let (m1, _) = AlgorithmSignMachine::from_cache(algo.clone(), keys.clone(),
                                             CachedPreprocess(Zeroizing::new(seed)));
let (m1, s1) = m1.sign(preprocesses_set_a.clone(), msg).unwrap();

// Pass 2: same seed, attacker swaps one preprocess -> different rho vector,
// same included set, same msg (same lambda, same challenge c)
let (m2, _) = AlgorithmSignMachine::from_cache(algo, keys,
                                             CachedPreprocess(Zeroizing::new(seed)));
let (_m2, s2) = m2.sign(preprocesses_set_b.clone(), msg).unwrap();

// Recovery: e = (s1 - s2) / (rho1 - rho2); d = s1 - e*rho1 - lam*c*x_term
// then x = (s1 - d - e*rho1) / (lam * c)  -> victim secret share recovered.
```

Determinism of the nonces across both passes is guaranteed by `seeded_preprocess` (`crypto/frost/src/sign.rs:127-132`); the differing `rho` comes from `calculate_binding_factors` binding each participant's commitments (`crypto/frost/src/nonce.rs:161-173`), which the attacker controls via `read_preprocess` bytes. The concrete trigger is the coordinator caching keyed only on `context` and invoking `share_internal` (which calls `preprocess_internal` → `from_cache`) in both `DkgConfirmer::share` and `DkgConfirmer::complete` (`coordinator/src/tributary/signing_protocol.rs:123-147, 288-327`).