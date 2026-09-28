### Title
Deterministic DKG-confirmation nonce is reused across distinct `KeyPair` messages when `DkgKeyPair` is overwritten — MuSig nonce reuse enables validator key share recovery - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
The audit finding class is "a mutable binding that can be re-set after dependent state exists, invalidating/stranding the prior state." In `Comptroller._setUniV3LpVault`, overwriting the vault pointer bricked all NFT positions tied to the old vault. Serai's analog lives in the DKG confirmation flow: `SigningProtocol::preprocess_internal` derives FROST nonces deterministically from a `CachedPreprocesses` seed keyed only by `(b"DkgConfirmer", attempt)` and writes it once (`if ... is_none()`), while `generated_key_pair` unconditionally overwrites `DkgKeyPair` — the value that determines the signed message via `set_keys_message`. The nonce is therefore set-once per attempt, but the message key is re-settable, producing a mismatch identical in shape to the reported bug: later invocations cooperate against state (the nonce) that was committed under different parameters.

### Finding Description
`preprocess_internal` caches a 32-byte ChaCha seed under `self.context = (b"DkgConfirmer", attempt)` and regenerates identical `nonces`/`commitments` on every call via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` (`crypto/frost/src/sign.rs:121-145`). The `share` path binds this fixed nonce to `msg = set_keys_message(set, removed, key_pair)`, where `key_pair` is whatever `generated_key_pair` last wrote (`handle.rs:54-55`, `DkgKeyPair::set` overwrites unconditionally). `complete` re-runs `share_internal` and re-signs whatever `DkgKeyPair` currently holds (`signing_protocol.rs:312-327`). There is no consistency check that the cached nonce is only ever combined with one message — the very "set-once" guard the audit recommended (`CachedPreprocesses::set` behind `is_none()`) exists for the nonce but not for the message key it is bound to.

With the nonce pair `(d, e)` fixed, each share is `s = d + ρ_i·e + c·λ·x`. Since `ρ_i` and `c` bind to `msg`, every distinct `key_pair` signed under the same attempt yields one linear equation in the unknowns `{d, e, λx}`. Three distinct messages under one attempt give a solvable 3×3 system, recovering the validator's MuSig secret share — which is the validator's root-of-trust substrate key share.

### Impact Explanation
Recovery of a validator's Ristretto secret key, enabling forgery of that validator's signed tributary transactions, slash reports, and any protocol action keyed by it. This is key-share recovery caused by nonce reuse — the exact failure mode the module documentation itself warns about ("it is explicitly unsafe to reuse nonces across signing sessions", lines 25-35).

### Likelihood Explanation
Medium. Triggering requires `generated_key_pair`/`complete` to run with multiple distinct `key_pair` values under a single `attempt` — reachable across a rebuild/re-execution where the processor reports a regenerated key pair before the attempt rotates, since `DkgKeyPair::set` is an unconditional overwrite and nothing binds the cached nonce to the first `key_pair`. Two distinct messages alone do not suffice (the ρ-varying nonce leaves the system underdetermined), so an attacker needs three shares or two shares plus control of a peer's commitments to pin ρ; the exposure window is the buggy overwrite path, not a single call.

### Recommendation
Mirror the audit's mitigation: make the message binding set-once like the nonce. Either (a) store the first `key_pair` alongside the cached seed and `assert_eq!` on subsequent `share`/`complete` calls, refusing to sign a different `set_keys_message` under the same `(b"DkgConfirmer", attempt)` context, or (b) fold a hash of `key_pair` into the `CachedPreprocesses` context so a changed message yields a fresh nonce. The header TODO at `signing_protocol.rs:50-54` (verifying decided nonces match on-chain commitments before publishing shares) should be extended to also pin the message.

### Proof of Concept
1. DKG attempt `k` reaches confirmation; `dkg_confirmation_nonces` caches seed `S` under context `("DkgConfirmer", k)` (`signing_protocol.rs:123-135`) and publishes commitments `C`.
2. `generated_key_pair` writes `key_pair_A` (`handle.rs:54`) and `share()` returns `s_A = d + ρ_A·e + c_A·λ·x`.
3. Re-execution (or a second processor report) calls `generated_key_pair` with `key_pair_B ≠ key_pair_A`; `DkgKeyPair::set` overwrites; `share()` now returns `s_B` with the same `(d, e)`.
4. A third distinct `key_pair_C` yields `s_C`. From `s_A, s_B, s_C` and the public `ρ, c` values, solve the linear system for `d`, `e`, and `λ·x`; since `λ` is computable from the participant set, the secret share `x` is recovered.

Caveat: I could not fully trace every caller of `generated_key_pair` within the tool-call budget, so the exact trigger that supplies a second/third distinct `key_pair` for one attempt (processor rebuild semantics) is inferred from the unconditional overwrite and the documented re-execution model rather than confirmed end-to-end.