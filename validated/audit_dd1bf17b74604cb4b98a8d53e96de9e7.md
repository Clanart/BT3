The strongest analog is the `CachedPreprocesses` reuse path in the coordinator. Let me check the callers and whether the cache is ever invalidated.### Title

Deterministic nonce reuse via context-keyed `CachedPreprocesses` enables validator key-share recovery - (File: `coordinator/src/tributary/signing_protocol.rs`)

### Summary

`SigningProtocol::preprocess_internal` stores the FROST preprocess RNG seed in the DB keyed only by `context = (b"DkgConfirmer", attempt)` and **never deletes or marks it consumed**. Every call to `share_internal` (via `DkgConfirmer::share` *and* again inside `DkgConfirmer::complete`) reloads that same seed and rebuilds an `AlgorithmSignMachine` with identical raw nonces `(d, e)` via `AlgorithmMachine::seeded_preprocess` / `ChaCha20Rng::from_seed`. The analog to the kernel UAF: a nonce that should have been freed/consumed after first use is silently revived and reused whenever the signing session is re-executed under the same context.

### Finding Description

- The seed is created once per context and persisted: `CachedPreprocesses::set(self.txn, &self.context, &cache.0)` (`signing_protocol.rs:134`). There is no corresponding removal; `share_internal` calls `preprocess_internal` again (`signing_protocol.rs:156`), regenerating the same `nonces` (`sign.rs:127-141`).
- `DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal` (`signing_protocol.rs:309`, `:322-324`), so the same `(d, e)` nonces sign twice per attempt by construction. This is only safe if the `preprocesses` map and `msg` are byte-identical on both calls — `complete` accepts `preprocesses` as an independent argument (`signing_protocol.rs:314`), supplied from received peer preprocesses fed through `read_preprocess` (`signing_protocol.rs:164-166`).
- If the preprocess set differs between invocations — e.g., `share` executes once a threshold set is seen and `complete` re-executes after additional participant preprocesses arrive, or a peer's malformed-then-replaced preprocess alters the `included` set — the binding factors `rho_i` and the participant list change while `d` and `e` do not (`nonce.rs:161-173`, `sign.rs:325-371`). The signer emits `s_i = d + e·rho_i + c_i·λ·x` for each variant.
- The file's own header acknowledges this hazard: nonce reuse is "explicitly unsafe" and safety is asserted only from BFT re-execution determinism (`signing_protocol.rs:25-48`), with an open TODO to verify on-chain preprocesses match the presumed ones (`signing_protocol.rs:50-54`). Nothing enforces equality of the preprocess set across `share`/`complete` calls.

### Impact Explanation

A participant who causes the validator to sign twice under the same `attempt` context with differing preprocess maps (public inputs they control via `Commitments::read`/`read_preprocess`) obtains two shares `s1 = d + e·rho1 + c1·λx` and `s2 = d + e·rho2 + c2·λx` with identical `(d, e)`. Because the adversary chooses their own commitments adaptively, this is the ROS/deterministic-nonce-reuse setting: by crafting preprocesses that make the challenges/`rho`s linearly related (standard Drijvers-style attack on FROST-like schemes with reused nonces), they recover `d`, `e`, and then the validator's Ristretto private key share `x`. That key is the validator's MuSig root-of-trust key used for on-chain DKG confirmation — forging further confirmation signatures and effectively impersonating the validator.

### Likelihood Explanation

Medium. Exploitation requires two `share_internal` executions under one `(b"DkgConfirmer", attempt)` context with differing preprocess sets or `key_pair`. The code explicitly relies on BFT-finalized inputs being identical across re-executions, and `share`/`complete` receiving different preprocess maps is not guaranteed by the protocol flow — but it is also not *enforced* anywhere; `share` then `complete` signing with the same nonces is structural, so any deviation in the received preprocess set (extra arrivals, ordering, or a crafted replacement before finality assumptions hold) reuses `(d, e)`. The unprivileged party only needs to supply preprocess bytes, which is reachable.

### Recommendation

Make the cached preprocess single-use: after `share_internal` signs, record the exact `included` participant set and `msg` hash alongside the seed (or delete the seed and persist the produced share), and refuse to sign again for the same context unless inputs are byte-identical, in which case return the previously produced share rather than re-deriving nonces. Alternatively, derive the nonce seed as `H(seed || commitments_hash || msg)` so distinct sessions cannot reuse `(d, e)` even under cache reloads.

### Proof of Concept

Conceptual trace (call sequences are in `DkgConfirmer`):

1. Attempt `k`: `share(preprocesses_A, kp)` → `share_internal` → `preprocess_internal` loads seed `S` for `("DkgConfirmer", k)`, regenerates `nonces = (d, e)` (`sign.rs:127-133`), emits `s1 = d + e·rho1 + c1·λx` (`algorithm.rs:201-211`).
2. `complete(preprocesses_B, kp, shares)` where `preprocesses_B` contains one additional participant preprocess (adversary-supplied, parsed via `read_preprocess` at `signing_protocol.rs:164`) → `share_internal` again loads `S`, same `(d, e)`, but `included`, per-participant `rho_i`, and hence `c` differ (`nonce.rs:180-212`, `sign.rs:361-371`) → emits `s2 = d + e·rho2 + c2·λx`.
3. Adversary, having chosen their commitments in both sets to control the relation between `rho1, rho2, c1, c2` (ROS attack), solves for `d`, `e`, and `λx` → recovers the validator's key share. Two malicious shares under distinct linearly-related challenges suffice per the standard nonce-reuse linear algebra; the structure is identical to FROST nonce reuse the library itself warns about (`sign.rs:85-87`, FROST.md "Caching" section).

*Unverified caveat:* whether `handle.rs` can actually deliver differing `preprocesses` maps to `share` vs `complete` under one attempt could not be fully confirmed within the available search iterations; the structural reuse (same seed → same nonces on every `share_internal` call) is confirmed in the code above.