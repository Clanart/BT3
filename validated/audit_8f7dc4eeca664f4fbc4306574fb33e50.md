### Title
Deterministic preprocess seed permits nonce reuse across sessions, enabling private key share recovery - ([File: crypto/frost/src/sign.rs])

### Summary
`AlgorithmSignMachine` derives its entire nonce state deterministically from the 32-byte `CachedPreprocess` seed via `ChaCha20Rng` in `seeded_preprocess`, and `from_cache` performs no consumption marker, deletion, or attempt binding. Rebuilding a machine from the same seed reproduces the identical nonce pair `(d, e)` and identical preprocess commitments. When shares for two or more distinct messages are produced from the same nonce, the secret key share is recoverable by anyone holding the signature shares — the same "stale reference to a consumable" shape as CVE-2017-6874's get/put mismatch: the cache is a one-time resource that is fetched (`from_cache`) without being destroyed, so a later fetch still returns it.

### Finding Description
In `seeded_preprocess` (`crypto/frost/src/sign.rs:121-144`), the nonces and `blame_entropy` are drawn from `ChaCha20Rng::from_seed(*seed.0)`, making the preprocess a pure function of the seed and the `ThresholdKeys`. `from_cache` (`sign.rs:268-274`) simply calls `seeded_preprocess(cache)`; nothing invalidates the seed, no session/attempt ID is mixed in, and the doc-comment only documents the MUST-use-once contract without enforcing it.

During `sign` (`sign.rs:386-398`), the effective nonce is `actual*rho + base` (i.e., `d + e·rho`), and `sign_share` emits a share of the form `s = d + e·rho + λ·x·c`. If the same seed seeds two `AlgorithmSignMachine`s which sign different `(msg, preprocess-set)` tuples, the shares are `s_i = d + e·rho_i + λ·x·c_i`. `rho_i` and `c_i` are publicly computable from the broadcast preprocesses/message, `λ` is public Lagrange data, so `d, e, x` are the only unknowns — a linear system that three distinct `(rho, c)` pairs solve uniquely, leaking the FROST secret share `x`.

Notably, `TransactionSignMachine::sign` in `networks/bitcoin/src/wallet/send.rs:378-395` runs *multiple* `sig.sign(...)` calls over different sighashes in one machine composition — the exact multi-message-per-nonce pattern, mitigated there only because each input uses a separately preprocessed nonce. Any integrator-level retry, replay, or parallel-session path that reconstructs a `SignMachine` from the persisted cache before the old one is retired (crash-recovery replay, concurrent workers reading the same DB row — the pattern visible in the coordinator's `CachedPreprocesses` handling) reaches this.

### Impact Explanation
High. Recovery of a participant's private key share `x` breaks the threshold assumption directly: combined with the public `ThresholdView`, an attacker who obtains `t` shares (or who compromises fewer shares than `t` otherwise could) can forge signatures. Even partial disclosure reduces effective security, since `x` is a linear share of the group secret. The leaked material is a long-lived secret, not a per-session artifact.

### Likelihood Explanation
Medium. Reuse requires the same `CachedPreprocess` to be loaded more than once, which the docs forbid — but the API gives no guard, the seed is explicitly designed to be persisted to disk (`cache()` exists precisely so machines survive restarts), and every restart/retry path must independently guarantee at-most-once consumption. This is a well-documented real-world failure mode for cached-nonce FROST designs, and the consequences are catastrophic rather than a DoS.

### Recommendation
- Mix a monotonic attempt/session counter or the signing context into the seed before `ChaCha20Rng::from_seed`, so identical persisted seeds for different sign sessions produce different nonces.
- Have `from_cache` take ownership semantics seriously at the storage layer: consumers should atomically get-and-delete the cache entry (compare-and-swap on the DB row) so a concurrent or repeated load fails.
- Zeroize/overwrite the stored cache on first successful `sign`, and consider storing a `used` flag checked in `from_cache` to turn silent reuse into a hard error.

### Proof of Concept
```rust
// crypto/frost — demonstrate share recovery from reused seed
use frost::{curve::Secp256k1, algorithm::Schnorr, *};
use rand_core::OsRng;

// Setup: keys from a DKG for participant set {1,2}, t=2 (omitted: standard).
let keys: ThresholdKeys<Secp256k1> = /* dkg output */ unimplemented!();
let alg = Schnorr::<Secp256k1>::new();

// One seed, persisted and loaded twice (retry/replay after crash).
let (machine_a, _) = AlgorithmMachine::new(alg.clone(), keys.clone()).preprocess(&mut OsRng);
let cache = machine_a.cache();                 // persisted to disk
let (m1, pp1) = SignMachine::from_cache(alg.clone(), keys.clone(), CachedPreprocess(Zeroizing::new(cache.0)));
let (m2, pp2) = SignMachine::from_cache(alg, keys, CachedPreprocess(Zeroizing::new(cache.0)));

// pp1.serialize() == pp2.serialize(): identical commitments, identical (d, e).
assert_eq!(pp1, pp2);

// Sign two different messages against the same peer preprocess set.
let peers = /* HashMap<Participant, Preprocess> from co-signers */ unimplemented!();
let (_sig1, share1) = m1.sign(peers.clone(), b"message A").unwrap();
let (_sig2, share2) = m2.sign(peers.clone(), b"message B").unwrap();

// share1.0 = d + e*rho_A + lam*x*c_A ; share2.0 = d + e*rho_B + lam*x*c_B
// With a third session (or a differing co-signer set changing rho), the linear
// system {d, e, x} is uniquely solvable -> x (private key share) recovered.
```
The shares `share1`, `share2` are broadcast over the authenticated channel as normal protocol output, so any participant (or network observer of the share round) holds the equations needed; `rho`/`c`/`λ` are all publicly derivable, leaving only the nonce scalars and `x` unknown.