### Title
Deterministic CachedPreprocess reuse across messages enables key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to CVE-2019-20529 (a private artifact exposed to anyone holding the reference), `SigningProtocol::preprocess_internal` persists a FROST nonce seed in `CachedPreprocesses` and reuses it for every signing round under the same context. FROST explicitly forbids preprocess/nonce reuse because a signature share leaks the secret share; here the cached seed — retrievable by anyone who can induce a second `share`/`complete` under the same `(b"DkgConfirmer", attempt)` context — deterministically reproduces the same nonces for different messages.

### Finding Description
`preprocess_internal` XORs the machine's `CachedPreprocess` seed with `Blake2s256("Cached Preprocess Encryption Key" || context || key)` and stores it in `CachedPreprocesses` keyed only by `context` (signing_protocol.rs:107-134). On subsequent calls, the same seed is reloaded and passed to `AlgorithmSignMachine::from_cache` (signing_protocol.rs:137-147). In `crypto/frost/src/sign.rs` (`seeded_preprocess`, lines 121-144) the seed feeds `ChaCha20Rng`, so `Commitments::new` regenerates identical nonces every time.

`DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal`, which rebuilds the sign machine from that same cache (lines 288-324). The cache is never cleared after a successful or failed signing attempt. The signed message is `set_keys_message(set, removed, key_pair)` (lines 296-301) — a caller-controlled byte string via `key_pair`. The FROST docs in this repo state the requirement explicitly: "A preprocess MUST only be used once. Reuse will enable third-party recovery of your private key share" (crypto/frost/src/sign.rs:85-87; spec/cryptography/FROST.md:51-55).

### Impact Explanation
A Schnorr/MuSig share is `s = nonce + c·key_share`. If two shares are produced under the same context with different `msg` (different `key_pair`, or a different signing set changing the binding factors/challenge), the identical nonce yields `key_share = (s1 − s2)/(c1 − c2)`. Any participant who receives or observes the two signature shares — public protocol messages broadcast on authenticated channels — recovers the victim's MuSig private key share. Even absent a differing message, `complete` re-signs after `share`, so any divergence in the preprocess set across the two calls (an attacker can withhold/alter their preprocess between calls, changing per-participant binding factors ρ and the challenge) produces two shares reusing the nonce with different challenges. This is unauthorized disclosure of a key share — direct secret leakage reachable entirely with public inputs.

### Likelihood Explanation
`share` and `complete` are separate entry points; `share` may be invoked again with a modified preprocess map or a different `key_pair` before `complete`. Because `CachedPreprocesses` is keyed solely on `(b"DkgConfirmer", attempt)` and is never deleted/rotated, every invocation within an attempt reuses the nonce. An unprivileged fellow validator controls the contents and ordering of preprocesses it submits and can trigger repeated `share` evaluations. No leaked key, collusion, or malformed curve input is required — only causing two share computations under one context.

### Recommendation
Delete `CachedPreprocesses::get(context)` (or overwrite the entry) after the first `share_internal` call for that context, and refuse to `share`/`complete` a second time unless the context is bumped (e.g., include a per-signing-attempt nonce or the canonical `key_pair`/preprocess-set hash in the DB key so divergent inputs cannot reuse the seed). Alternatively, derive the nonce seed as `H(seed || msg || sorted_preprocesses)` inside `seeded_preprocess` so any input change yields fresh nonces.

### Proof of Concept
1. In one DKG confirmation attempt (`context = (b"DkgConfirmer", attempt)` fixed), call `DkgConfirmer::share(preprocesses, key_pair_A)` → share `s1` over nonce `R` for message `m_A`.
2. Submit an altered `key_pair_B` (or a preprocess map differing in one entry, changing the binding factor/challenge) and call `share`/`complete` again → share `s2` over the same nonce `R` (same cached seed → same `ChaCha20Rng` → same nonces, `sign.rs:127-132`) for `m_B`.
3. Compute `key_share = (s1 − s2) · (c1 − c2)⁻¹` where `c_i` are the Schnorrkel challenges; verify `key_share·G` equals the victim's verification share.

Uncertain aspects: I did not trace every caller of `DkgConfirmer::share`/`complete` to confirm a validator can unilaterally force a second `share` with divergent `key_pair` within one attempt; however, `complete` itself unconditionally re-invokes `share_internal` (line 321-324), so any difference between the preprocess set supplied to `share` and to `complete` — which an attacker controls via its own preprocess message — already suffices for nonce reuse with differing challenges.