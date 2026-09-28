### Title
FROST nonce reuse via never-released cached preprocess in the coordinator's DKG confirmation signing leaks validator key shares - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
CVE-2024-53074 is a missing resource release on teardown: a link mapping is never freed on AP removal, so a stale resource poisons all subsequent uses of the same context. The direct Serai analog lives in `SigningProtocol::preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:100-148`): a FROST preprocess seed is generated once per `context` (for `DkgConfirmer`, `context = (b"DkgConfirmer", attempt)`, line 275), stored in `CachedPreprocesses` (line 134), and is **never released or rotated**. Every subsequent `share()` and `complete()` call under the same attempt re-derives the identical nonces via `AlgorithmSignMachine::from_cache` (line 145), which feeds the same 32-byte seed into `ChaCha20Rng` → `Commitments::new` (`crypto/frost/src/sign.rs:121-144`).

### Finding Description
The file's own safety argument (lines 25-54) admits the scheme is only safe if `sign` is never re-executed over distinct nonce commitments or a distinct message. That invariant does not hold:

- `share()` invokes `share_internal` → `preprocess_internal` → `machine.sign(preprocesses, msg)` (lines 150-180), where `preprocesses` is a caller-supplied map of whichever validators' preprocesses were received so far.
- `share()` returns `Err(Participant)` on `FrostError::InvalidPreprocess` (lines 170-178). A retry after such a failure — same attempt, same context — reuses the identical nonce seed with a *different* signing set, hence different per-participant binding factors `rho` and a different FROST challenge.
- `complete()` also calls `share_internal` again (lines 321-324) to rebuild the signature machine. If the preprocess set passed to `complete` differs from the set used when `share()` was originally invoked (e.g., a straggler validator's preprocess finalized in a later tributary block — no reorganization required, since messages are simply appended on the same finalized chain), the same nonce is bound to a different commitment list.
- Additionally, the signed message itself embeds `self.removed` (`set_keys_message`, lines 296-300). Any drift in the removed set derived for the same attempt produces a distinct message under the same nonce.

This is exactly the kernel bug's shape: the cached seed is a per-context resource that is allocated once but never released when a signing attempt is retried/re-executed, so the stale resource (fixed nonce) contaminates later protocol runs under that context.

### Impact Explanation
Two signature shares produced with the same nonce under different challenges are publicly solvable for the signer's secret share: `s = k + c·λ·x`, so `(s₁ - s₂)/(c₁' - c₂')` (with the differing `rho`/challenge terms public) recovers the validator's MuSig/Ristretto secret. Both shares are published on the public tributary, so any observer can perform the recovery — the attacker only needs to read finalized chain data. Compromise of the validator's signing key breaks the root-of-trust assumption the entire coordinator design rests on (file header, lines 1-14): the attacker can forge `set_keys` confirmations. Severity: **High**.

### Likelihood Explanation
Trigger requires two `share`-equivalent executions under one attempt with different inputs: a retry after `InvalidPreprocess`, or a `complete()` whose preprocess set grew relative to the set at `share()` time. The latter requires only ordinary message-ordering timing among *honest* validators on a single finalized chain — the BFT argument in the comments only rules out *conflicting* finalized messages, not an accumulated superset of preprocesses between two calls. No malicious validator, collusion, or leaked key is needed as precondition.

### Recommendation
Bind the nonce decision to the full signing input, or consume the resource on use:
1. Delete/rotate the `CachedPreprocesses` entry after a successful `share_internal`, and key the DB entry on a hash of (context, sorted preprocess set, msg) so that any change of signing set or message deterministically selects a distinct seed — re-execution with identical inputs stays safe, differing inputs cannot reuse nonces.
2. Emit the node's own signature share only from the single canonical `share()` path (have `complete()` reuse the already-published share rather than re-signing via `share_internal`), eliminating the second `sign` execution entirely.
3. Enforce the documented TODO (line 51): before publishing a share, verify on-chain that the commitments match this node's presumed preprocess, so a divergent execution is caught instead of leaking the nonce pair.

### Proof of Concept
Conceptual trace on `DkgConfirmer` for attempt `a`:

1. Context `(b"DkgConfirmer", a)`; first `share()` call: `CachedPreprocesses::get` is `None`, seed `S` is generated and persisted (lines 123-134). `share_internal` signs with preprocess set `P = {p₁,…,p_t}` and publishes share `s₁ = d + e·c₁·λ₁·x` on-chain.
2. A later block finalizes an additional validator preprocess `p_{t+1}`; the node calls `share()`/`complete()` again with `P' = P ∪ {p_{t+1}}` (or a retry occurs after an `InvalidPreprocess` error dropped a bad entry).
3. `preprocess_internal` reloads the same `S` (line 137-145) → identical nonces `d, e`; but `sign` computes different `rho` values and a different challenge `c₂` over `P'` and `msg`.
4. Any observer with `s₁`, `s₂` and the public transcripts solves the two linear equations for `x`, the validator's secret key share.

Supporting code: `coordinator/src/tributary/signing_protocol.rs:123-148` (seed cached once, reused unconditionally), `:150-180` (`share_internal` re-signs under same seed), `:312-327` (`complete` re-invokes `share_internal`), `:296-300` (message depends on `removed`), and `crypto/frost/src/sign.rs:121-144` (seed → `ChaCha20Rng` → deterministic nonces, guaranteeing reuse).