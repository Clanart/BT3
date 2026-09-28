### Title
Deterministic cached FROST preprocess reused across distinct messages leaks the validator's secret share — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives the FROST signing nonce deterministically from a `CachedPreprocess` seeded RNG keyed only by `(b"DkgConfirmer", attempt)`. The same cached seed is reloaded and reused for every `share`/`complete` call within a DKG attempt, so if two distinct `set_keys_message` payloads are signed under one attempt context, the same nonce is used twice — a textbook nonce-reuse key disclosure, the Serai analog of CVE-2022-1016's "improperly handled path leaks secret information".

### Finding Description
`DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal`, which calls `preprocess_internal` (line 156), which regenerates the `AlgorithmSignMachine` from `CachedPreprocesses` stored under the fixed context `("DkgConfirmer", self.attempt)` (lines 274–277). The comment at lines 104–106 acknowledges the cached preprocess is a secret equivalent to the private key, and lines 50–54 contain TODOs admitting the "on-chain-preprocess-matches-presumed-preprocess check" is not yet implemented.

Because `seeded_preprocess` (crypto/frost/src/sign.rs:121-144) deterministically derives both nonces `d, e` via `Commitments::new` → `Curve::random_nonce` hashing `seed || secret`, reloading the same seed yields the identical `d` and `e`. The signed message is `set_keys_message(set, removed_participants, key_pair)` (line 296), which varies with `key_pair` and `removed`. If `share`/`complete` is invoked twice in the same attempt with different `key_pair`/`removed` values — e.g., a distinct `DkgCommitments`/`KeyGen` outcome being re-attempted under the same attempt counter, or a caller passing a different `key_pair` — the identical nonce `d + ρ·e` is committed to two different Schnorrkel challenges. Two shares `s1 = d + ρe + λ_i·c1·share_i` and `s2 = d + ρe + λ_i·c2·share_i` let any observer solve for `share_i` directly (crypto/frost/src/sign.rs:447-460 sums shares into the Schnorr `s` value). Since this is a MuSig share over the validator's private key via `musig(...)` (line 119), recovery of the share with the known aggregation coefficient recovers validator-key material.

### Impact Explanation
An unprivileged party who can cause a second distinct message to be signed under one DKG attempt (a distinct `key_pair` or `removed` set reaching `share_internal`) obtains the signing participant's FROST secret share / MuSig key contribution from two published shares. That is full key-share recovery — the exact "local, unprivileged information leak of secret data" consequence of the CVE — and in the worst case enables forgery of `set_keys` confirmations.

### Likelihood Explanation
Likelihood is moderate: it requires the coordinator to evaluate `share`/`complete` on two distinct messages within one attempt, which the design intends BFT finality to prevent. However, the safety argument rests entirely on "distinct finalized messages can't occur" plus acknowledged-missing consistency checks (lines 50–54 TODOs), and `complete` itself calls `share_internal` a second time, relying on message equality rather than enforcing it — any logic path that varies `key_pair` or `removed` between calls (re-execution after partial rebuild, as the header itself discusses) triggers the leak.

### Recommendation
Bind the cached preprocess to the exact message being signed: include a hash of `set_keys_message` output (or the `key_pair`/attempt tuple) in the `CachedPreprocesses` context key, or store the decided message alongside the seed and refuse to sign any different message with the same seed. Additionally, consume (delete) the cached preprocess on first use rather than reloading it.

### Proof of Concept
1. In one DKG `attempt`, let `share_internal` be invoked with `key_pair_A`, producing share `s1` over `msg_A` with nonce `D = d·G + ρ·e·G` derived from the cached seed for context `("DkgConfirmer", attempt)`.
2. Invoke `share_internal` (or `complete`, which re-runs `share_internal`) with `key_pair_B != key_pair_A`; the same cached seed reproduces the identical `d, e`, producing `s2` over `msg_B` with the same `D`.
3. From the two public shares and the two known challenges `c1, c2` (recomputable from `msg_A`, `msg_B`), compute `share_i = (s1 - s2) / (λ_i (c1 - c2))`, recovering the participant's secret share — private key material for the MuSig validator key.

Uncertainty: the exact coordinator call sites that feed `key_pair`/`removed` into `DkgConfirmer` were not fully traced within available iterations; the vulnerability is conditional on a second distinct message reaching `share_internal` under a single attempt, which the in-code comments indicate is possible during rebuild/re-execution but guarded only by BFT assumptions and an unimplemented TODO check.