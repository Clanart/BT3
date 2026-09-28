### Title
Repeated `share`/`complete` calls within one DKG attempt reuse the cached FROST preprocess, re-signing with identical nonces and leaking the secret share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
CVE-2021-20179 is a "replayable authorization" flaw: a compromised credential could be renewed indefinitely because nothing invalidated prior state. The Serai analog lives in the coordinator's tributary signing protocol: a cached FROST preprocess, keyed only by `(b"DkgConfirmer", attempt)`, is reloaded on every `share_internal` call and is never consumed, invalidated, or bound to a specific message or signing set. Any caller who can trigger `share`/`complete` more than once for the same attempt obtains multiple Schnorr signature shares produced with identical nonces.

### Finding Description
`SigningProtocol::preprocess_internal` derives the preprocess deterministically from a seed stored in `CachedPreprocesses` under `self.context`, and regenerates identical `nonces`/`commitments` via `seeded_preprocess` / `ChaCha20Rng::from_seed`. The cache entry is only written when absent; it is never cleared after use:

- Cache write-once, never invalidated: `coordinator/src/tributary/signing_protocol.rs:123-145`
- Deterministic nonce regeneration from the seed: `crypto/frost/src/sign.rs:121-143` (`seeded_preprocess`)
- `share_internal` unconditionally calls `preprocess_internal`: `coordinator/src/tributary/signing_protocol.rs:156`
- `DkgConfirmer::share` and `DkgConfirmer::complete` both funnel into `share_internal` with context `(b"DkgConfirmer", attempt)`: `coordinator/src/tributary/signing_protocol.rs:274-326`
- `handle.rs` invokes these paths when processing peer preprocess/shares messages (9 call sites), so a remote validator reaching the coordinator with tributary messages controls how many times, and with which preprocess sets, `share_internal` runs.

Because the nonces are fixed by the seed, varying either (a) the signing set (different `preprocesses` maps → different `included` → different binding factors ρ, different Lagrange coefficients, different effective challenge) or (b) the message (`key_pair` → different `set_keys_message`) yields two shares `s₁ = d + ρ₁e + λ₁c₁x` and `s₂ = d + ρ₂e + λ₂c₂x` over the same `d, e`. With ρ, λ, c all publicly computable from the transcript and published preprocesses, this is a linear system in the nonce and the secret share `x` — solvable whenever the two share equations differ, which a malicious peer can force by controlling which preprocesses are included or by changing the co-signers between calls.

The FROST spec explicitly warns this reuse "will enable third-party recovery of your private key share" (`crypto/frost/src/sign.rs:85-87`, `spec/cryptography/FROST.md:51-54`), yet the coordinator layer reuses the seed for every share/complete within an attempt and relies only on context uniqueness — nothing enforces single-use at the point of signing, and `complete` itself performs a second `sign` after `share` already consumed the machine.

### Impact Explanation
Recovery of the validator's MuSig/FROST secret share contributes toward threshold key compromise of the set key (`musig(...)` in `preprocess_internal`), enabling an attacker who accumulates shares to forge Substrate signatures confirming malicious `KeyPair`s — i.e., attacker-controlled validator-set keys for Serai. This is confidentiality/integrity loss of the signing key, matching the CVE's "renew indefinitely unless revoked" shape: the one-time-use guarantee of the preprocess is violated because there is no revocation/consume step.

### Likelihood Explanation
Reachability requires causing `share`/`complete` to execute multiple times for one `(b"DkgConfirmer", attempt)` context with differing inputs. A peer able to emit tributary preprocess/shares messages (a validator in the set, or anyone whose messages are routed into `handle.rs`'s DKG-confirmer handlers) can submit varied preprocess maps; each submission re-runs `share_internal` on the same seed. Even without malicious intent, `complete` re-invoking `share_internal` demonstrates the double-sign path exists structurally. The attacker must be a participant able to inject preprocess messages — plausible for any validator — so likelihood is moderate; impact is high.

Caveat: I could not fully confirm whether `handle.rs` deduplicates repeated preprocess/shares messages per attempt before calling `share`/`complete`; if it enforces exactly-once semantics over distinct messages, the exploit narrows to the `share`-then-`complete` double-sign, which still reuses the same nonces on an identical message/set (same share → benign) — the vulnerability holds if any path varies the signing set or message between calls.

### Recommendation
Treat the cached preprocess as single-use: delete `CachedPreprocesses::get` entry (or store a consumed flag) once `share_internal` signs, and bind the cached context to the exact signing set/message (e.g., include a hash of `preprocesses`/`msg` in the context or reject reuse when they differ). Alternatively, persist the produced `AlgorithmSignatureMachine` state so `complete` does not re-derive and re-sign.

### Proof of Concept
1. Validator V participates in DKG attempt `a`; context is `(b"DkgConfirmer", a)`.
2. V sends preprocesses set P₁ to the coordinator; coordinator calls `share_internal` → seed regenerated → nonces (d, e), share `s₁` produced and broadcast.
3. V sends a different preprocess set P₂ (different participant subset or different commitments for a participant) for the same attempt; `share_internal` regenerates identical (d, e) but computes different binding factors/challenge, producing `s₂`.
4. From `s₁, s₂`, published commitments, ρ₁, ρ₂, λ₁, λ₂, c₁, c₂, V solves the two-equation linear system to recover the coordinator node's secret share of the MuSig-aggregated set key.
5. Repeating across validators (or combining with shares obtained via parallel sessions, ROS-style) recovers enough shares to sign `set_keys_message` for an attacker-chosen `KeyPair`.