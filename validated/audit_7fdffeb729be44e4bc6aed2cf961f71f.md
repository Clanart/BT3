### Title
Cached FROST preprocess seed reused across DKG confirmation sessions causes nonce reuse and validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists the FROST `CachedPreprocess` (the RNG seed from which all signing nonces are deterministically derived) in `CachedPreprocesses`, keyed only by `context = (b"DkgConfirmer", attempt)`. The seed is never deleted and the context does not include the validator set, genesis, or message. When a second DKG confirmation runs with the same `attempt` number (attempts restart per DKG session), `share_internal` reuses the identical seed, producing identical FROST nonces over a *different* `set_keys_message`. Two published signature shares under the same nonce with different challenges allow third-party recovery of the validator's MuSig secret key — the root-of-trust key.

### Finding Description
- `CachedPreprocesses` is a DB table keyed by `context` alone (`coordinator/src/tributary/signing_protocol.rs:86-90`).
- `DkgConfirmer::signing_protocol` sets `context = (b"DkgConfirmer", self.attempt)` — no set ID, no genesis, no message binding (line 275).
- `preprocess_internal` writes a fresh seed only `if CachedPreprocesses::get(...).is_none()` (lines 123-135), then loads and decrypts the stored seed and calls `AlgorithmSignMachine::from_cache` (lines 137-145). The entry is never removed after use.
- `share_internal` re-invokes `preprocess_internal` (line 156) and signs `set_keys_message(set, removed, key_pair)` (lines 296-301). `DkgConfirmer::new` computes `attempt` via `removed_as_of_dkg_attempt(txn, spec.genesis(), attempt)` (line 271), so attempt numbering is per-spec/per-DKG and repeats across sets.
- In `crypto/frost/src/sign.rs`, `from_cache` → `seeded_preprocess` → `ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new` (lines 121-133, 268-274): the same seed yields the same nonces `d, e`. The crate itself documents that reuse "will enable third-party recovery of your private key share" (`crypto/frost/src/sign.rs:85-87`; `spec/cryptography/FROST.md:51-53`).
- The file's own header acknowledges reuse is "explicitly unsafe" and relies solely on context-binding for safety (lines 25-35) — but the context collides across DKG sessions sharing an attempt index.

### Impact Explanation
A signature share is `d + ρ·e + λ·s·c`. Reusing `(d, e)` across two confirmations with distinct `msg`/binding factors yields two equations that solve for `s` — the validator's private MuSig key. Both shares are publicly broadcast to the tributary, so any observer recovers the key, enabling forgery of the validator's on-chain confirmations and any other signatures made with that key. This is direct secret-key recovery, not merely DoS.

### Likelihood Explanation
Triggering requires a second DKG confirmation under the same `(b"DkgConfirmer", attempt)` context for the same validator key — i.e., a later validator-set rotation whose confirmation reaches the same attempt number. DKG sessions restart attempt numbering, and the message necessarily differs (`set` and `key_pair` are set-specific), so nonce reuse across distinct messages occurs in normal protocol operation without any malicious participant or BFT violation.

### Recommendation
Include the set/session (e.g., `spec.set()` or `spec.genesis()`) in the DB context so the cached seed can never collide across DKG confirmations, and delete the `CachedPreprocesses` entry once the share is produced. Additionally, before publishing a share, verify on-chain that the commitments derived from the cached seed match the preprocess commitments the coordinator actually published (the file already flags this as a TODO at line 51).

### Proof of Concept
1. Validator set S₁ runs a DKG; during confirmation attempt `a`, `DkgConfirmer::share` stores/loads seed `X` under key `(b"DkgConfirmer", a)` and publishes share `σ₁` over `set_keys_message(S₁, removed₁, kp₁)`.
2. Later, set S₂ runs its DKG; confirmation again reaches attempt `a`. `CachedPreprocesses::get((b"DkgConfirmer", a))` returns `X` (never deleted), so `from_cache` regenerates the identical nonces `(d, e)` and publishes `σ₂` over `set_keys_message(S₂, removed₂, kp₂)`.
3. An observer collects both published shares. Since `σᵢ = d + ρᵢ·e + λ·s·cᵢ` with known `ρᵢ, cᵢ` and identical `d, e`, the two linear equations solve for the secret `s` — the validator's MuSig private key.

Uncertainty note: I could not fully read `coordinator/src/tributary/handle.rs` to confirm every caller path, but the root cause — seed persistence keyed on a context that repeats across DKG sessions — is established directly in `signing_protocol.rs`.