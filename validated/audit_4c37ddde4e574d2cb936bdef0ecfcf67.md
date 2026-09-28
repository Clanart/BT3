### Title
Cached FROST preprocess reused across distinct signing contexts enables validator key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Zebra's bug was a verification cache that treated "verified once" as "valid forever," ignoring that validity is context-dependent (block height). Serai's coordinator has the same bug shape in `SigningProtocol::preprocess_internal`: a FROST preprocess seed is cached in the DB keyed only by a `context` value — `("DkgConfirmer", attempt)` — that does not bind the validator set (session/genesis), the removed-participant list, the signing participant set, or the message. Because `AlgorithmSignMachine::from_cache` regenerates the same nonces deterministically from the cached seed, any second use of the same context key with different signing inputs produces a Schnorr nonce reuse, which leaks the signer's secret key share.

### Finding Description
`preprocess_internal` derives a deterministic FROST preprocess from a cached seed stored under `CachedPreprocesses: (context: &impl Encode)` (`coordinator/src/tributary/signing_protocol.rs:86-90`). The only production context is `DkgConfirmer`, which sets `context = (b"DkgConfirmer", self.attempt)` (line 275). The context omits:

- `spec.set()` / `spec.genesis()` — so two different validator sets (sessions/tributaries) running attempt 0 collide on the same cache key.
- `self.removed` — the removed-participant list used in `threshold_i_map_to_keys_and_musig_i_map` and in the signed `set_keys_message`.
- The received preprocess map and the `key_pair` — i.e., the actual signing inputs.

`share_internal` (line 156) calls `preprocess_internal` to rebuild the machine, then `machine.sign(preprocesses, msg)`. `DkgConfirmer::share` and `DkgConfirmer::complete` both reach `share_internal` (lines 304-327): `complete` re-executes `share_internal` with whatever `preprocesses` and `key_pair` map it is handed. If the preprocess set or `key_pair` differs between the two invocations — or a later session with the same `attempt` index is signed — the identical seed yields identical nonces while the binding factors/aggregate challenge/message differ. The header comment (lines 25-54) concedes safety depends entirely on the BFT layer never feeding distinct messages under the same context; the cryptographic code itself provides no protection — the cache lookup cannot distinguish the contexts.

Two Schnorr shares `s = d + c·x` and `s' = d + c'·x` with the same nonce `d` and different challenges `c ≠ c'` yield `x = (s - s')/(c - c')` — recovery of the validator's MuSig/FROST secret share. Since `share`/`complete` shares are published on-chain/tributary, an observer can compute the share from public data.

### Impact Explanation
Recovery of a validator's Ristretto secret share — the root-of-trust key used to confirm DKG results on Substrate. Combined with fewer than threshold other compromises this degrades toward threshold reconstruction; even alone, a leaked validator key enables forgery of that validator's contributions to future MuSig confirmations. This is analogous to the Zebra advisory: a cached "already-decided" artifact (preprocess seed vs. mempool verification) is replayed into a context where it is no longer the same decision, converting a performance/recovery optimization into a consensus-critical key compromise.

### Likelihood Explanation
Reachability does not require malicious validators: the same node will naturally produce a second `share_internal` call when `complete` re-executes signing, and validators commonly persist across consecutive `ExternalValidatorSet`s where `attempt` restarts at 0 while `("DkgConfirmer", 0)` remains the DB key. A trigger would be any case where the same key signs under the same `(label, attempt)` context with different participant sets or a different `set_keys_message` — e.g., a validator present in two consecutive sets, or `share` called with one preprocess set and `complete` called with another (the code only `.expect`s success, it does not verify the preprocess map matches the first call's). One residual uncertainty: I could not confirm in `coordinator/src/tributary/handle.rs` exactly which tributary messages drive `share`/`complete` and whether preprocess-map diversity is reachable purely from unprivileged traffic; the exposure is real regardless because normal multi-session operation is sufficient.

### Recommendation
Bind the cache key to every input that affects signature validity: include `spec.genesis()` or `spec.set()`, the `removed` set, and the hash of the preprocess set/`key_pair` in `context`. Additionally, mirror the FROST contract (`sign.rs:209-224`: cached preprocesses MUST be used once) by recording "share emitted for context X with digest H" and refusing to sign if H differs — the TODO at line 51 (verify on-chain commitments match the presumed preprocess before publishing shares) is exactly this check and should be implemented. If a differing context is detected, derive a fresh seed rather than reusing the cached one.

### Proof of Concept
Conceptual flow (pseudocode, since triggering requires coordinator state):

```rust
// Session S, attempt 0: context = ("DkgConfirmer", 0)
let mut confirmer = DkgConfirmer::new(&key, &spec_a, &mut txn, 0).unwrap();
// publishes share s1 = d + c1 * x over msg1 = set_keys_message(set_a, removed_a, kp1)
let s1 = confirmer.share(preprocesses_p1, &kp1).unwrap();

// Same key, different set (or different removed/preprocesses), attempt 0:
// CachedPreprocesses::get(("DkgConfirmer", 0)) returns the SAME seed,
// from_cache regenerates identical nonces -> same d
let mut confirmer2 = DkgConfirmer::new(&key, &spec_b, &mut txn, 0).unwrap();
let s2 = confirmer2.share(preprocesses_p2, &kp2).unwrap(); // c2 != c1

// Anyone observing both published shares:
//   x = (s1 - s2) * (c1 - c2)^{-1}  (mod l)
//   d = s1 - c1 * x   -> full nonce AND validator secret share recovered
```

Root cause: `CachedPreprocesses` DB key `("DkgConfirmer", attempt)` (`signing_protocol.rs:275`, `:88`) does not encode `spec.set()`, `removed`, or the signing inputs, so the "cache hit" condition fails to capture everything the signature depends on — the same failure mode as caching verification without the height it was verified at.