### Title
Cached FROST preprocess seed is never deleted after signing, permitting deterministic nonce reuse across `share`/`complete` re-execution - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
CVE-2021-47022 is a cleanup-ordering defect: `mt7615_unregister_device()` freed pending TX state without first invoking `mt7615_tx_token_put()`, so a required teardown step never ran. The analog in Serai is identical in shape: `SigningProtocol::preprocess_internal` persists a deterministic preprocess seed in `CachedPreprocesses` and **never removes it after the signing session finishes**. The FROST API explicitly requires that after `from_cache`, "the preprocess must be deleted so it's never reused" (`crypto/frost/src/sign.rs`, `SignMachine::from_cache` docs). Because the seed is regenerated deterministically from the same cached bytes on every call, each subsequent `share_internal`/`complete_internal` under the same context re-derives identical nonces — the very failure mode the mandatory cleanup exists to prevent.

### Finding Description
`preprocess_internal` at `coordinator/src/tributary/signing_protocol.rs:100-148` does:

1. If `CachedPreprocesses::get(txn, &context)` is empty, runs `preprocess(&mut OsRng)`, caches the machine's `CachedPreprocess` seed XORed with a key-derived encryption key, and stores it (lines 123-134).
2. Loads the cached seed and rebuilds the sign machine via `AlgorithmSignMachine::from_cache` (lines 137-145), which deterministically regenerates the nonces via `ChaCha20Rng::from_seed` in `seeded_preprocess` (`crypto/frost/src/sign.rs:127-132`).

There is no corresponding `CachedPreprocesses::` deletion anywhere in the crate; the grep for the DB accessor shows only `get`/`set` in this file. The missing "put back / delete" step is the direct analog of the missing `mt7615_tx_token_put()`.

The re-execution hazard is real, not hypothetical: `DkgConfirmer::complete` (lines 312-327) calls `self.share_internal(preprocesses, key_pair)` again to reconstruct the signature machine — a second `sign()` over the same nonces. Worse, the signed message is `set_keys_message(&self.spec.set(), &self.removed, key_pair)` (lines 296-300), where `key_pair` is a caller-supplied argument. If `share` and `complete` — or two `share` invocations — are invoked under the same `(b"DkgConfirmer", attempt)` context with different `key_pair`s (or different preprocess sets, which change the FROST binding factor `rho`), the same nonce `k` signs distinct challenges `c1 != c2`, yielding two shares `s1 = k + c1·x`, `s2 = k + c2·x` that recover the private share `x = (s1 - s2)/(c1 - c2)`.

The safety comment at lines 25-48 acknowledges that nonce reuse under re-execution is "explicitly unsafe" and relies on BFT ordering guaranteeing identical messages, with an admitted unchecked TODO: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)". The missing delete means that guarantee is the only thing standing between correct operation and secret-share recovery — and the code itself provides no enforcement, unlike the FROST API contract it consumes.

### Impact Explanation
Reuse of a Schnorr nonce across two distinct (message, signing-set) pairs yields two linear equations in the secret key share, enabling algebraic recovery of the coordinator's MuSig/Ristretto private key share by anyone who collects both broadcast signature shares. Since signature shares are published to all validators, an unprivileged party who can cause `share`/`complete` to be invoked with differing preprocess sets or key_pairs under the same attempt context obtains the victim validator's private share — full compromise of that validator's signing capability (critical-scale impact against the validator root of trust).

### Likelihood Explanation
The precondition is narrower than arbitrary reuse: the danger only materializes if `share`/`complete` are invoked multiple times under one `(context)` with different inputs — e.g., a `share` followed by a `complete` carrying a different `key_pair`, or a partial rebuild (explicitly acknowledged as a hazard in the file header at line 47: "This does set a bound preventing partial rebuilds which is accepted"). The code relies on BFT finality making inputs identical; a logical flaw or partial DB rebuild that lets distinct preprocesses/messages reach `share_internal` converts the never-deleted cache directly into key-share recovery. Medium-to-high conditional severity: catastrophic impact gated behind a re-execution-with-distinct-inputs scenario the design claims but does not enforce.

### Recommendation
After the signing session for a context completes (or the share is published), delete the entry: add a `CachedPreprocesses::kill`-style removal (or `del`) and call it once the share/signature is finalized — mirroring `mt7615_tx_token_put()` before teardown. Additionally, enforce the documented invariants: before publishing a share, verify the locally-derived commitments match the preprocess actually finalized on-chain (the existing TODO), and reject `share`/`complete` invocations whose `key_pair`/preprocess set differs from what was already signed under the same context, storing the signed msg hash alongside the cached seed.

### Proof of Concept
Conceptual trace (no code execution available):

1. Attempt `a` begins; `DkgConfirmer::share(preprocesses_A, key_pair_1)` stores seed under `("DkgConfirmer", a)` and publishes share `s1` for `msg1 = set_keys_message(set, removed, key_pair_1)`.
2. `DkgConfirmer::complete(preprocesses_A, key_pair_2, shares)` is invoked (or a partial rebuild re-executes with different inputs). `share_internal` → `preprocess_internal` reloads the identical seed → identical nonces; `sign()` computes `msg2 = set_keys_message(set, removed, key_pair_2)` with `msg2 != msg1` → challenge `c2 != c1`.
3. The resulting share `s2` (or the completed signature's internal shares) combined with `s1` gives `x = (s1 - s2)·(c1 - c2)^-1`, recovering the validator's private share `x` — exactly the "third-party recovery of your private key share" that `SignMachine::from_cache` warns about and that deletion of the cache is mandated to prevent.