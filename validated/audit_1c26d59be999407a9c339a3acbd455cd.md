The most promising analog: `CachedPreprocesses` in `signing_protocol.rs` is never deleted — the same seed is reloaded on every `share_internal` call for the same context. Let me check the context and whether multiple calls can happen per context.### Title
Cached FROST nonce seed reused across distinct sign invocations under one context enables key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` deterministically re-derives the FROST nonce pair `(d, e)` from a seed stored in `CachedPreprocesses` keyed only by `context`, and the entry is never consumed or rotated. `share_internal` is invoked once by `DkgConfirmer::share` and again by `DkgConfirmer::complete`, each time with a `preprocesses` map and `key_pair` supplied by the caller at that moment. If the two invocations receive different preprocess sets (or a different `key_pair`, which changes `msg` and hence `rho`), the same binomial nonce `(d, e)` is used across two distinct signing sessions — the exact failure mode the file's own header calls "explicitly unsafe". This is the Serai analog of the Vyper reentrancy bug class: a one-shot resource (the preprocess seed, like the reentrancy guard/liquidity) is re-entered while still live because no lock/consumption is enforced.

### Finding Description
In `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:100-148), a 32-byte seed is generated once per `context` and stored via `CachedPreprocesses::set`; every later call loads the same seed and rebuilds identical `Nonce`s via `AlgorithmSignMachine::from_cache`. The file documents that safety relies on never signing distinct received messages under one context (lines 25-48).

However, `DkgConfirmer::share` (lines 304-310) and `DkgConfirmer::complete` (lines 312-327) each call `share_internal` with independently supplied arguments. `complete` does not reuse the `Preprocess` map `share` was invoked with; it takes a fresh `preprocesses: HashMap<Participant, Vec<u8>>` and `key_pair` from the new call. Because the seed is fixed, signing twice with:

- a different subset of `t` preprocesses, or
- the same preprocesses but a different `key_pair` (msg = `set_keys_message(set, removed, key_pair)`)

produces two shares `s_j = d + e·rho_j + c_j·λ·x` with known distinct `rho_j`, `c_j` and identical `(d, e, x)`. With three such invocations the linear system determines `d`, `e`, and the Lagrange-weighted secret share `λ·x`; with two invocations plus the published aggregate signature the share is likewise recoverable.

### Impact Explanation
Recovery of a validator's MuSig/FROST secret share breaks the root-of-trust confirmation signature used to ratify DKG `KeyPair`s on-chain (`set_keys_message`), i.e. an unintended-message signing capability against the validator key. Per `crypto/frost/src/sign.rs` docs, preprocess reuse "will enable third-party recovery of your private key share" — here the reuse is forced by the protocol's stateless re-execution rather than caller misuse.

### Likelihood Explanation
Requires the same `(b"DkgConfirmer", attempt)` context to be signed over distinct `(preprocesses, key_pair)` tuples. The distinct tuples must appear in distinct finalized tributary transactions — both `share` and `complete` legitimately carry attacker-influenced `preprocesses` bytes and `key_pair` as transaction data, so no BFT violation is required for the *inputs* to differ; only that the coordinator executes both paths before the attempt ends. This is narrower than a free-standing ROS attack since `rho` binds `group_key`, `msg`, and the full preprocess transcript, so honest duplicate calls produce identical shares — exploitation needs a submitter able to place a differing `preprocesses`/`key_pair` payload into a finalized transaction for the same attempt.

### Recommendation
Consume the cached seed: delete `CachedPreprocesses[context]` (or store a "spent" marker and the exact committed `preprocesses`/`key_pair` hash) when the first share is produced, and refuse to sign if a subsequent invocation presents different session inputs. Alternatively persist the signed `(preprocesses, msg)` alongside the seed and require byte-equality before re-signing, implementing the "on-chain-preprocess-matches-presumed-preprocess check" already noted as a TODO at lines 51-54.

### Proof of Concept
1. In attempt `a`, a transaction causes `DkgConfirmer::share(P1, K)` to run, publishing share `s1 = d + e·rho1 + c1·λ·x`.
2. A later finalized transaction for the same attempt calls `DkgConfirmer::complete(P2, K', shares)` where `P2 ≠ P1` or `K' ≠ K`; `share_internal` reloads the same seed from `CachedPreprocesses`, yielding `s2 = d + e·rho2 + c2·λ·x` with `rho2 ≠ rho1`.
3. Collect a third share under the same context (or use the published aggregate). Solve the linear system for `x` (and `d`, `e`), recovering the validator's signing key share.