### Title
Deterministic CachedPreprocess reuse lets a delayed/duplicated `share` call sign twice with the same nonce, enabling validator key-share recovery - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
Analogous to CVE-2026-74730 (a delayed operation acting on state whose lifetime was not pinned), `SigningProtocol::preprocess_internal` deterministically reloads the *same* cached preprocess seed from the DB every time it is invoked for a given context, and never consumes/invalidates it. Because `share_internal` rebuilds the signing machine by re-calling `preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:156`), any second execution of `share` under the same `context` — e.g., a delayed or re-driven call operating on a different set of peer preprocesses or a different `key_pair`/`msg` — reuses identical FROST nonces under a different binding factor/challenge, leaking the validator's MuSig secret share.

### Finding Description
`preprocess_internal` encrypts and stores `CachedPreprocesses` keyed only by `self.context`, then unconditionally loads that same seed and rebuilds nonces via `AlgorithmSignMachine::from_cache` → `seeded_preprocess`, which regenerates `nonces`/`commitments` deterministically from `ChaCha20Rng::from_seed` (`crypto/frost/src/sign.rs:121-144`, `signing_protocol.rs:123-147`). There is no "consume on use" step: `CachedPreprocesses::get` is re-read on every call and the entry is never deleted. The file's own header admits the hazard: nonce reuse occurs if "the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" and explicitly notes a missing check (`signing_protocol.rs:34-54`).

`share_internal` (`signing_protocol.rs:150-181`) calls `preprocess_internal().0` to reconstruct the machine, parses attacker-supplied preprocess bytes via `machine.read_preprocess`, then `machine.sign(preprocesses, msg)`. `DkgConfirmer` fixes `context = (b"DkgConfirmer", self.attempt)` (`signing_protocol.rs:275`), while both `msg` (built from `key_pair` in `signing_protocol.rs:296-301`) and the preprocess map (peer-supplied bytes, re-mapped by `threshold_i_map_to_keys_and_musig_i_map`) are per-call inputs. So for a fixed attempt, `DkgConfirmer::share` invoked twice — or once plus `complete`, which internally re-runs `share_internal` (`signing_protocol.rs:312-327`) — with differing peer preprocess sets or a different `key_pair` produces two signature shares using identical `(d, e)` nonces but different aggregate `R`/challenge.

### Impact Explanation
With two shares `z_i = d + e·b_i + λ_i·s·c_i` over the same nonce but different binding factors `b_i` and challenges `c_i`, a co-signer (whose preprocess bytes are public protocol inputs parsed via `read_preprocess`) can solve the linear system and recover the victim's secret share `s` — full compromise of that validator's MuSig signing key, matching the documented consequence: "Reuse will enable third-party recovery of your private key share" (`crypto/frost/src/sign.rs:85-87`).

### Likelihood Explanation
Requires the same-`attempt` `share` path to execute more than once over differing inputs — precisely the "delayed call racing ahead of state" shape of the reference bug (e.g., a coordinator-side retry/re-execution delivering a distinct preprocess batch or mutated `key_pair` before completion). The missing on-chain-consistency check the authors flag as TODO removes the guard that would otherwise detect this.

### Recommendation
Bind the cached preprocess to a single decision: record the serialized preprocesses and `msg` digest alongside the cached seed, and refuse (or deterministically reproduce the identical output for) any `share`/`complete` invocation whose inputs differ from the first execution — implementing the on-chain-preprocess-match check already marked TODO at `signing_protocol.rs:50-54`. Alternatively, burn the `CachedPreprocesses` entry upon first `share`.

### Proof of Concept
1. Validator runs `DkgConfirmer::new(key, spec, txn, attempt)`; `preprocess()` publishes commitments derived from seed `S` stored under `(b"DkgConfirmer", attempt)`.
2. First `share(P_A, key_pair)` emits `z_1` over nonce commitments aggregated from `P_A` and `msg_1`.
3. A delayed/duplicated `share(P_B, key_pair')` (distinct peer preprocess batch or mutated key pair, same `attempt`) reloads `S` — identical nonces — and emits `z_2` under different `b`, `c`.
4. Observer solves for `s` from `(z_1, z_2)`, recovering the validator's private key share.