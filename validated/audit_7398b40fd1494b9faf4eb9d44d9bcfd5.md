### Title
Cached FROST nonce reused across `share`/`complete` re-executions with different messages enables secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator's MuSig signing protocol deterministically derives its FROST nonce from a cached seed keyed only by `(b"DkgConfirmer", attempt)` (`CachedPreprocesses`). Both `DkgConfirmer::share` and `DkgConfirmer::complete` call `share_internal`, which re-derives the same nonce and calls `sign` on a message (`set_keys_message`) and a binding-factor set that are computed from the caller-supplied `key_pair` and `preprocesses` arguments. If these arguments differ between invocations — or between two `share` calls — the same nonce is used to sign two different challenges, allowing anyone who observes both signature shares to solve for the validator's MuSig secret share.

### Finding Description
In `preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:123-145`), if `CachedPreprocesses::get(txn, &context)` is `None`, a fresh seed is generated, XOR-encrypted, and stored; otherwise the existing seed is reused via `AlgorithmSignMachine::from_cache`, which calls `seeded_preprocess` and regenerates identical nonces from `ChaCha20Rng::from_seed(*seed.0)` (`crypto/frost/src/sign.rs:127-141`).

The cache key is only `(b"DkgConfirmer", self.attempt)` (`signing_protocol.rs:275`). It does **not** bind to the message or to the set of preprocesses. The signed message is `set_keys_message(&self.spec.set(), &removed, key_pair)` where `key_pair` is an argument to each call (`signing_protocol.rs:296-301`), and the preprocess map is re-parsed per call from `serialized_preprocesses` (`signing_protocol.rs:158-168`).

`complete` re-executes `share_internal` (`signing_protocol.rs:321-324`), so the nonce is consumed a second time. The file's own header (`signing_protocol.rs:34-48`) acknowledges that nonce reuse occurs if the received commitments or message differ across re-executions, and relies entirely on BFT finality guaranteeing identical inputs — with an explicit `TODO` (line 51) noting the on-chain commitment check is not implemented.

The analog to the Tokemak finding is exact: `destinationInfo` is stale state read at redeem-time instead of being refreshed; here `CachedPreprocesses` is stale signing state replayed at share-time instead of being bound to — or invalidated against — the actual inputs being signed.

### Impact Explanation
FROST signature shares have the form `share = d + b*rho + lambda*s + e*offset`-style linear relations in the nonce `r`, binding factor, and secret share `s` under challenge `c`. Two shares produced under the same nonce commitments `R` but different challenges `c1 ≠ c2` (different `msg`, different aggregate nonce from different preprocess sets, or different `key_pair`) yield two equations `z_i = r_i·bound + λ_i·s·c_i`, letting an observer solve for `s`. The file itself states "recovery of it will enable recovering the private key" (`signing_protocol.rs:104`). Recovery of a coordinator validator's MuSig secret share compromises the validator-set root of trust used to confirm DKG results on-chain.

### Likelihood Explanation
Reachability: the differing inputs are `key_pair` and `preprocesses`, which arrive via tributary transactions — i.e., data submitted by validators (public inputs fed to `read_preprocess`). A validator who can get two conflicting preprocess sets or two distinct `key_pair` values finalized across the calls `share`/`complete` triggers reuse. The design assumes BFT finality makes finalized data immutable for a given context; the gap is that `complete` accepts `preprocesses` and `shares` as fresh arguments rather than re-reading the exact finalized set used for `share`, and there is no check that the regenerated nonce commitments match what was published (the acknowledged TODO at line 51). Because exploitability requires conflicting validator-submitted data reaching distinct calls, likelihood is bounded by the BFT assumption — but the cryptographic failure mode is unconditional once inputs differ, and the code documents this check as unimplemented. Medium likelihood, High impact.

### Recommendation
- Include the message hash and the hash of the preprocess set (or the committed preprocess bytes) in the `CachedPreprocesses` context key, so a different message/set derives a different nonce and re-execution with identical inputs remains deterministic.
- Alternatively, persist the published preprocess commitments alongside the seed and, in `share_internal`/`complete`, assert the locally regenerated commitments equal the ones already published on-chain before signing — implementing the TODO at `signing_protocol.rs:51`.
- Delete `CachedPreprocesses` for a context once `complete` succeeds, so no third call can replay the nonce.

### Proof of Concept
1. `DkgConfirmer::new` with `attempt = k` creates context `(b"DkgConfirmer", k)`; first `share(preprocesses_A, key_pair_A)` stores and consumes seed `S`, producing share `z1` over `msg_A = set_keys_message(set, removed, key_pair_A)`.
2. A subsequent call — `share(preprocesses_B, key_pair_B)` or `complete(preprocesses_B, key_pair_B, shares)` — reloads the same seed `S` (cache key unchanged), regenerates identical nonces via `seeded_preprocess`, and calls `sign` with a different aggregate nonce/challenge `c2`, producing `z2`.
3. An observer of `z1`, `z2` solves the two linear equations for the validator's secret share `s`, recovering the MuSig private key share (`crypto/frost/src/sign.rs:283+` share production; nonce reuse ⇒ share recovery as documented at `crypto/frost/src/sign.rs:85-87`).

Caveat: I was unable to fully trace `coordinator/src/tributary/handle.rs` to confirm whether `share` and `complete` can legitimately be invoked with diverging finalized inputs for the same `attempt`; the vulnerability's reachability rests on the documented-but-unimplemented commitment check (TODO, line 51) and on validator-submitted preprocess data being treated as the binding input rather than a fixed finalized value.