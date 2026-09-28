### Title
Deterministic nonce reuse across repeated signing executions via cached preprocess lacks synchronization/guard — FROST secret share recoverable - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary

The referenced Grafana bug (CVE-2023-2801, CWE-662/CWE-820) is a missing-synchronization flaw: independent executions share mutable state, and concurrent/repeated requests produce a crash. The analog in Serai is the signing protocol's `CachedPreprocesses` handling: every execution deterministically regenerates the *same* FROST nonces from a single DB-persisted seed keyed only by `context`, and no guard synchronizes or invalidates the seed between `sign` executions. If `share_internal` is reached twice under the same context with differing preprocess sets or messages — which the file's own header admits is possible and notes an unimplemented check for — the same nonce signs two distinct bindings, enabling third-party recovery of the validator's private key share.

### Finding Description

In `preprocess_internal`, the preprocess seed is stored in `CachedPreprocesses` keyed solely by `context` and is never deleted or rotated after signing:

```rust
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
    // generate + store cache
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
...
let (machine, preprocess) =
    AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

`from_cache` routes to `seeded_preprocess`, which seeds `ChaCha20Rng::from_seed(*seed.0)` and derives `nonces`/`commitments` purely deterministically (crypto/frost/src/sign.rs:121-144). Every call to `preprocess_internal` under the same `context` therefore produces an `AlgorithmSignMachine` with **identical nonces**. Both `DkgConfirmer::share` and `DkgConfirmer::complete` call `share_internal` → `preprocess_internal` → `machine.sign(preprocesses, msg)`, i.e., `sign` is executed multiple times against the same fixed nonces, and the inputs (`preprocesses` map from other participants, `msg` = `set_keys_message(...)`) come from externally-supplied data.

The file's own header acknowledges the hazard (lines 25-54): nonce reuse across sessions is "explicitly unsafe"; safety is only argued from BFT ordering and absence of partial rebuilds, and the note explicitly states the protective check that regenerated commitments match the on-chain/finalized preprocesses is **not implemented** ("we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)"). Because the seed is not invalidated after a share is produced and there is no synchronization tying one seed to exactly one signing instance, any path that re-executes `sign` with a different `preprocesses` map or different `key_pair`/`msg` under the same `(b"DkgConfirmer", attempt)` context reuses `(d, e)` nonces on a distinct binding factor `b`/`rho` and challenge `c`. Two shares `s1 = k + c1·λ·x`, `s2 = k + c2·λ·x` (with `k` the reused nonce and `c1 ≠ c2`) yield `λ·x = (s1 − s2)/(c1 − c2)`, recovering the threshold secret share — exactly the "preprocess reuse enables key share recovery" consequence documented at crypto/frost/src/sign.rs:83-92.

### Impact Explanation

Recovery of a validator's MuSig/FROST secret key share. This signing protocol produces Schnorrkel signatures confirming DKG results on Substrate (`set_keys_message`); recovering a validator's share enables forging its contribution to confirmation signatures and undermines the threshold assumption of the validator set root-of-trust. This is secret-key material leakage, analogous in severity to the high-impact result of the Grafana flaw (instance compromise rather than mere crash), and matches the "key share recovery" accept criterion.

### Likelihood Explanation

Requires a re-execution of `share_internal` under an existing context with differing inputs: e.g., a coordinator restart/partial rebuild where the DB persisted the `CachedPreprocesses` entry (so nonces are regenerated deterministically) while the ordering of received preprocess messages or the key-pair confirmation data being signed differs from the earlier execution — the exact "partial rebuild" boundary the header documents as unprotected due to the missing TODO commitment check. Other participants' preprocess messages and the confirmation transaction data are the untrusted inputs steering the divergence. Likelihood is moderate-to-low because the BFT layer normally fixes the input set, but the cryptographic layer itself contains no synchronization or single-use enforcement, and the divergence case is explicitly known to the authors.

### Recommendation

- After producing a share, invalidate or mark-consumed the `CachedPreprocesses` entry in the same DB transaction, so any subsequent `share_internal` under the same context fails rather than reuses the seed.
- Implement the noted TODO: before publishing a share, verify the commitments derived from the cached seed match this participant's already-finalized preprocess on-chain; abort on mismatch.
- Bind the `context` additionally to a hash of the finalized preprocess set / message so a different input set deterministically selects a different seed.

### Proof of Concept

1. Coordinator executes `DkgConfirmer::share(preprocesses_A, key_pair)` for attempt `a`: `preprocess_internal` stores seed `S` under context `(b"DkgConfirmer", a)` and `seeded_preprocess` derives nonces `(d, e)` via `ChaCha20Rng::from_seed(S)`; the produced share is `s1 = k + c1·λ·x` where `c1` binds preprocess set A and message `m_A`.
2. After a partial rebuild/re-execution (the case the header acknowledges is unguarded because the on-chain commitment check is a TODO), `share_internal` runs again under the same context: `CachedPreprocesses::get` returns `S`, `from_cache` regenerates the identical `(d, e)`. If the finalized preprocess set or `key_pair` differs (`preprocesses_B`, `m_B`), the emitted share is `s2 = k + c2·λ·x` with `c2 ≠ c1`.
3. Any observer collecting `s1` and `s2` computes `λ·x = (s1 − s2)·(c1 − c2)^{-1} mod l`, recovering the validator's secret share. No cryptographic flaw in FROST itself is needed — only the missing single-use synchronization on `CachedPreprocesses`.