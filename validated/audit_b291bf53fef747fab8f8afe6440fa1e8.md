### Title
`DkgConfirmer` reuses a cached FROST nonce across distinct signing contexts within one attempt, enabling validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The OpenClaw advisory describes unsafe secret-bearing state being replayed after the surrounding context changes (request body replayed across a cross-origin redirect). The Serai analog is `SigningProtocol::preprocess_internal` in `coordinator/src/tributary/signing_protocol.rs`, which deterministically regenerates FROST nonces from a DB-cached seed keyed only by `context`, while `DkgConfirmer::share` / `DkgConfirmer::complete` may sign inputs that vary independently of that context — the `KeyPair` message and the attacker-supplied preprocess set. Reusing a FROST nonce under two distinct challenges leaks the long-lived secret share linearly, the exact failure `CachedPreprocess` warns about.

### Finding Description
`SigningProtocol::preprocess_internal` stores a random 32-byte seed in `CachedPreprocesses` under `self.context`, then decrypts and feeds it to `AlgorithmSignMachine::from_cache` on every call for that context. The seed fully determines the signing nonces: `seeded_preprocess` seeds `ChaCha20Rng` with it and derives `nonces`/`commitments` deterministically.

`DkgConfirmer` sets `context = (b"DkgConfirmer", self.attempt)` — it does **not** bind the `KeyPair` being confirmed, the `removed` validator set, or the set of preprocesses. Yet all three feed the signed message or the signature challenge:

- `share_internal` builds `msg` via `set_keys_message(&self.spec.set(), &self.removed..., key_pair)` and calls `machine.sign(preprocesses, msg)`.
- `share()` and `complete()` each call `share_internal` independently. `complete()` re-runs `share_internal` with a fresh, externally supplied `preprocesses` map (`threshold_i_map_to_keys_and_musig_i_map` maps whatever map the caller passes). The preprocesses map determines `included`, the per-participant binding factors `rho`, and hence the challenge.
- `self.removed` is recomputed per call site via `removed_as_of_dkg_attempt`, which can change over time within the same attempt as removals are recorded.

So two `share_internal` invocations under the same `attempt` can produce two signature shares `s_i = d_i + e_i*rho_i*lambda_i + c_i*share_i` with identical nonces `d_i, e_i` but different `rho_i`/`c_i` (different preprocess set or different msg). Solving the two linear equations yields the validator's MuSig secret share — the exact "reused seed ⇒ private key share recovery" failure documented for `CachedPreprocess`. The file's own safety argument (lines 25–48) assumes nonce reuse can only occur if received messages differ, which it asserts is impossible under BFT ordering — but the nonces are cached at *context* granularity while `share` and `complete` re-derive inputs from caller-supplied data each time, and the code itself flags the analogous gap for processor preprocesses in the TODO at line 53–54.

### Impact Explanation
Recovery of a validator's MuSig secret share breaks the DKG-confirmation signing protocol: combined with `t-1` other leaked shares or via rogue-key style exploitation it enables forging `set_keys` confirmations — i.e., signing an unintended message — which is the root-of-trust operation confirming validator group keys on Substrate. Even short of full key recovery, a malicious participant who submits crafted preprocess bytes can cause the coordinator to emit two shares under one nonce, satisfying the "concrete key share recovery" bar.

### Likelihood Explanation
Reachability hinges on `share`/`complete` being invoked more than once per attempt with divergent inputs. `complete` unconditionally re-runs `share_internal`, so any divergence between the preprocess map used at `share` time and the one passed to `complete` (which is attacker-influenced, since preprocesses are serialized bytes received from other validators) triggers nonce reuse. Additionally, if `share` fails once and is retried with a different `KeyPair` or after a new removal alters `self.removed`, the msg differs under the same cached seed. I did not fully trace `handle.rs` call sites, so the exact trigger path is partially verified; however the `preprocesses`/`key_pair`/`removed` inputs are demonstrably not bound into the nonce context, which is the root cause.

### Recommendation
Bind the nonce cache key to everything that enters the signature challenge: extend `context` to include a hash of `participants`, the serialized preprocess set, and `msg` (or store the seed under `(context, msg_hash, preprocess_set_hash)`), so any change in signed content forces a fresh nonce. Alternatively, make `SigningProtocol` single-shot per context: persist the signed share and have `complete` reuse the already-produced machine/share rather than re-running `share_internal`. Also implement the TODO check (lines 51–54) verifying the preprocess published on-chain matches the presumed cached preprocess before publishing shares.

### Proof of Concept
Conceptual reproduction against `coordinator/src/tributary/signing_protocol.rs`:

1. Assume validator V runs `DkgConfirmer` for `attempt = A`, caching seed `S` under `(b"DkgConfirmer", A)` in `CachedPreprocesses`.
2. Call `confirmer.share(preprocesses_P, key_pair_K)` → produces share `s1` over msg `m1 = set_keys_message(set, removed_P, K)` with nonces from `S`.
3. Call `confirmer.complete(preprocesses_Q, key_pair_K', shares)` where the attacker-supplied `preprocesses_Q` differs from `preprocesses_P` (different signer subset or different commitment bytes) → `share_internal` regenerates the same nonces from `S` but computes a different challenge `c2` (different `included` set ⇒ different `rho` and different `hash_msg`).
4. With `s1 = d + e·rho1·λ + c1·x` and `s2 = d + e·rho2·λ + c2·x` (known `d·G, e·G` from the published commitments, known `rho1, rho2, c1, c2, λ`), solve the 2×2 linear system for `d` (or `e`), then recover V's secret share `x` directly.
5. `x` is V's MuSig participation share for `musig_context(set)`, enabling forgery of `set_keys` confirmations / participation in rogue signing as V.

The core primitives (deterministic nonce derivation in `seeded_preprocess`, context-only cache keying, and independent re-derivation of msg/preprocesses in `share`/`complete`) are all verified in the cited code; only the orchestration-level guarantee that inputs never diverge between calls remains unverified in `handle.rs`.