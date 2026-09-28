### Title
Cached FROST preprocess seed is never invalidated — `share`/`complete` re-derive and reuse the same signing nonces, leaking the validator's private key share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Node.js UAF bug class maps onto Serai's signing code as *reuse of a consumed one-time resource*: a FROST preprocess/nonce that must be used exactly once is re-instantiated from a cached seed that is never deleted. In `SigningProtocol::preprocess_internal`, the `CachedPreprocess` seed stored under `CachedPreprocesses: (context) -> [u8; 32]` is read on every invocation and `AlgorithmSignMachine::from_cache` regenerates the identical `nonces` from `ChaCha20Rng::from_seed(*seed.0)` (`crypto/frost/src/sign.rs:127-132`). The seed is written once and never cleared after signing.

### Finding Description
`DkgConfirmer` sets `context = (b"DkgConfirmer", self.attempt)` (line 275), so all calls within one DKG-confirmation attempt share a single seed:

- `DkgConfirmer::share` → `share_internal` → `preprocess_internal` → `machine.sign(preprocesses, msg)` (lines 304-310, 288-302, 150-180).
- `DkgConfirmer::complete` → `share_internal` again → a *second* `machine.sign(...)` with the **same regenerated nonces**, then `complete_internal` (lines 312-327).

`msg` is `set_keys_message(&self.spec.set(), &removed..., key_pair)` where `key_pair` is taken from attacker-visible transaction data, and `preprocesses` are untrusted bytes parsed via `machine.read_preprocess` (line 165). `AlgorithmSignMachine::sign` computes the share as `d + b·rho + c·λ·share_i`; the FROST challenge `c` and binding factors `rho` depend on `hash_msg(msg)` and the `"preprocesses"` transcript of the included set (`crypto/frost/src/sign.rs:361-371`). Two shares produced with identical `(d, b)` but differing `rho`/`c` (different `key_pair`, different `removed` set, or a different participant preprocess set) allow solving linear equations for `secret_share`.

The file's own safety argument concedes the requirement: "In order for nonce re-use to occur, the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" (lines 34-35) — yet `complete()` unconditionally calls `sign` again, and the admitted mitigation ("check the commitments generated from the decided nonces are in fact its commitments on-chain", "on-chain-preprocess-matches-presumed-preprocess check") is an unimplemented `TODO` (lines 50-54). Nothing enforces that `share` and `complete` operate on identical inputs; the cache entry is neither deleted nor annotated as consumed.

### Impact Explanation
Two signature shares under the same nonce on distinct challenge contexts yield `secret_share` in the clear via standard Schnorr nonce-reuse algebra. Since `complete` re-runs `share_internal` with independently supplied `preprocesses`/`key_pair`, any divergence — distinct participant set, distinct removed list, distinct key pair — produces a second share on a different `(rho, c)`. Shares are published on the tributary, so any observer recovers the validator's MuSig/FROST private key share, breaking the validator-key root of trust used to confirm DKGs.

### Likelihood Explanation
Requires only that a `share` and a `complete` (or repeated `share`/`complete` invocations under the same attempt) be executed with non-identical preprocess sets or messages — inputs that are attacker-influenced bytes flowing through `read_preprocess`/`sign`, not requiring leaked keys, collusion at threshold, or broken BFT beyond the already-accepted design assumption. The nonces are deterministic given the seed, so the reuse is unconditional whenever inputs differ.

### Recommendation
- Delete/mark-consumed `CachedPreprocesses` once a share has been produced for a context, and refuse a second `sign`.
- Implement the noted TODO: before signing in `complete`, verify the on-chain preprocesses/message match the ones the original `share` committed to; otherwise abort.
- Reuse the `SignatureMachine` produced by the first `sign` for `complete` rather than regenerating nonces via `share_internal`.

### Proof of Concept
1. `DkgConfirmer::new(key, spec, txn, attempt)` — seed S stored under `(b"DkgConfirmer", attempt)`.
2. `share(preprocesses_A, key_pair_A)`: `from_cache` regenerates nonces `(d, b)` from S; emits share `s1 = d + b·rho_A + c_A·λ·x` for `msg_A = set_keys_message(.., key_pair_A)`.
3. `complete(preprocesses_B, key_pair_B, shares)` with `preprocesses_B != preprocesses_A` or `key_pair_B != key_pair_A`: `share_internal` again regenerates identical `(d, b)` and emits `s2 = d + b·rho_B + c_B·λ·x` internally — and `share()` can be invoked again to publish it.
4. Observer computes `x = (s1 - s2 - b·(rho_A - rho_B)) / (λ·(c_A - c_B))`, recovering the validator's secret share. Reaching `b·(rho_A - rho_B)` requires one more share or solving the 2-unknown system with a third share, all obtainable via repeated `share`/`complete` calls on the same context.

Caveat: I did not fully trace `coordinator/src/tributary/handle.rs` to confirm how divergent `preprocesses`/`key_pair` inputs reach `share` vs `complete`; the vulnerability stands on the never-invalidated cached seed and the unconditional second `sign`, with the file's own TODO confirming the missing consistency check.