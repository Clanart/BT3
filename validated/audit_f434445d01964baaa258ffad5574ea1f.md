### Title
Deterministic FROST nonce reuse across repeated `share` calls for a cached context enables secret-share recovery — ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
Analogous to the ed25519-dalek "double public key" oracle — where a signing function produces two shares over the same nonce while only the challenge (which binds the public key / message) varies — `SigningProtocol::share_internal` rebuilds the sign machine from the *same* `CachedPreprocess` seed on every invocation. Because `AlgorithmMachine::seeded_preprocess` derives FROST nonces deterministically from that seed alone (no message, participant set, or group-key binding), any second `share` call under the same protocol context emits a signature share computed over identical `(d, e)` nonces. Two such shares differing only in the effective challenge allow algebraic recovery of the signer's secret share, mirroring the "same R, different S → private key extraction" attack.

### Finding Description
In `coordinator/src/tributary/signing_protocol.rs:123-147`, `preprocess_internal` loads the cached 32-byte seed via `CachedPreprocesses::get`, decrypts it, and calls `AlgorithmSignMachine::from_cache` → `seeded_preprocess`. The seed is only written when absent (`CachedPreprocesses::set` at line 134) and is **never cleared or rotated after a share is produced**. In `crypto/frost/src/sign.rs:121-144`, `seeded_preprocess` seeds `ChaCha20Rng` with the cache and generates `Commitments::new` nonces purely from `original_secret_share` — independent of `msg`, the preprocess set, and `params.keys` tweaks. The resulting share in `crypto/frost/src/algorithm.rs:201-211` is `s = (d + e·ρ) + c·λ·x` where `c = H::hram(R, group_key, msg)`. Since `d` and `e` are context-fixed, any second `share_internal` call for the same `context` (e.g., `DkgConfirmer` context `(b"DkgConfirmer", attempt)` at line 275, whose `msg` = `set_keys_message(set, removed, key_pair)` varies with an externally supplied `key_pair` at lines 296-301) reuses `(d, e)`. With the same peer preprocesses, `ρ` and `R` are identical and `s₁ − s₂ = (c₁ − c₂)·λ·x`, yielding the secret share directly; with differing preprocesses, a small number of repeated shares solves the linear system ROS-style.

### Impact Explanation
Recovery of a validator's MuSig secret share (`self.key`), which is the validator's actual signing key (not merely a threshold share) for tributary operations. Combined with the deterministic nonce reuse, an attacker extracting `x` can forge the validator's signature shares and signatures, undermining the signing protocol's unforgeability — the same consequence class as the reported ed25519-dalek private-key extraction.

### Likelihood Explanation
Requires inducing a second `share_internal` call under an identical `context` with a differing effective challenge — e.g., a second confirmation/`key_pair` within one DKG attempt, or a repeated share request for the same context. Whether tributary transaction processing permits multiple `share`/`complete` invocations per attempt was not fully verified; the structural precondition (nonce determinism + persistent seed reuse at `signing_protocol.rs:137-145`) is confirmed by the code. Severity is High if reachable, Medium otherwise.

### Recommendation
After producing a share, delete or rotate the cached seed for the context (e.g., `CachedPreprocesses::remove`/one-shot flag), and/or mix the message and participant commitment transcript into `seeded_preprocess`'s nonce derivation so repeated calls cannot produce identical `(d, e)`.

### Proof of Concept
1. Validator calls `share(context)` for attempt `k` → share `s₁ = d + e·ρ + c₁·λx`.
2. A second share is produced for the same `(b"DkgConfirmer", k)` context with a different `key_pair` (different `msg`, same peer preprocesses): `s₂ = d + e·ρ + c₂·λx`, identical `R`.
3. Compute `x = (s₁ − s₂) / ((c₁ − c₂)·λ)` — the validator's private key share is recovered.