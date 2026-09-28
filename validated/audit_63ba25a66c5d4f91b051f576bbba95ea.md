### Title
FROST nonce reuse via undeleted `CachedPreprocesses` entry enables secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores the FROST preprocess seed in the `CachedPreprocesses` DB table keyed by the signing context, but never deletes it after use. Every subsequent call under the same context deterministically regenerates the identical FROST nonces (`d`, `e`). If `share_internal` is driven to `sign()` twice under one context with a different signing set or message — the exact "removed/freed object still referenced" shape of CVE-2024-24260's use-after-free in `sip_subscribe_remove` — the reused nonce leaks the validator's MuSig/FROST secret share via standard two-share linear algebra.

### Finding Description
`AlgorithmMachine::seeded_preprocess` derives all nonces and commitments deterministically from `ChaCha20Rng::from_seed(*seed.0)`, so a given `CachedPreprocess` seed fully determines the preprocess (crypto/frost/src/sign.rs:121-144). The API contract is explicit that this is single-use: "After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" (crypto/frost/src/sign.rs:216-224) and spec/cryptography/FROST.md:51-62 repeats that reuse enables third-party key-share recovery.

The coordinator violates this contract internally. In `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:123-147):

```rust
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
  // generate fresh seed, encrypt, store
  CachedPreprocesses::set(self.txn, &self.context, &cache.0);
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
// decrypt and rebuild machine
let (machine, preprocess) =
  AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

The entry is read but never removed. `share_internal` calls `preprocess_internal` again (line 156), and `complete` calls `share_internal` a second time (lines 321-326). The context is a fixed tuple per protocol instance, e.g. `(b"DkgConfirmer", attempt)` at line 275, so every `sign()` under that context uses identical nonces. The result is a deterministic-signing oracle: the same commitments `R = (D, E)` are reused across any distinct `sign()` invocations sharing a context.

### Impact Explanation
In FROST, a signature share is `s_i = d + b·e + λ_i·σ·c`, where `d, e` are the secret nonces (reused), `b` is the binding factor derived from the participant set and message, `c` the challenge, and `σ` the secret share. Two shares emitted with the same `(D, E)` but different `b`, `c`, or Lagrange coefficient `λ_i` — i.e., a different preprocess set or a different `set_keys_message` (which binds `removed` and `key_pair`, lines 296-300) — yield a linear system solvable for `σ`. The shares and preprocesses are published on the public tributary, so any observer recovers the validator's MuSig key share, meeting the "key share recovery" and "parallel-session share reuse / CachedPreprocess determinism" analog criteria. This is a reuse-of-consumed-state bug: the seed that the API mandates be destroyed is retained and replayed, mirroring the UAF class of the reference CVE.

### Likelihood Explanation
The reuse is structural — it happens on every `share()`/`complete()` pair. Whether two *divergent* signings occur under one context depends on coordinator retry paths: `share()` may be invoked, fail with `FrostError::InvalidPreprocess(l)`/`InvalidShare(l)` for a specific participant (lines 170-178, mapping to `InvalidParticipant` processor messages), and be re-driven with a modified preprocess map or message within the same `(label, attempt)` context; each retry reuses the seed. I could not fully trace every caller in `handle.rs`/`mod.rs` to confirm an unprivileged party can force a divergent second `sign()` under an identical context; the divergence requires either a changed signing set or a changed `key_pair`/`removed`-dependent message mid-attempt. The deterministic-reuse primitive itself is confirmed in the code.

### Recommendation
Delete the `CachedPreprocesses` entry atomically when it is consumed in `preprocess_internal` (e.g., a `take`-style DB operation rather than `get`), or generate the preprocess exactly once per context and carry the `AlgorithmSignMachine` forward instead of rebuilding from the seed on each `share()`/`complete()` call. Additionally, mix a per-invocation counter or the full intended signing-set/message hash into the context key so that any re-drive under the same logical session cannot regenerate identical nonces.

### Proof of Concept
Conceptual, based on crypto/frost/src/sign.rs and coordinator/src/tributary/signing_protocol.rs:

1. Validator publishes `Preprocess` with commitments `(D, E)` for context `(b"DkgConfirmer", attempt)` — generated from `CachedPreprocesses` seed `s`.
2. `share(preprocesses_A, key_pair)` produces `s_1 = d + b_A·e + λ_A·σ·c_A` over `msg_A = set_keys_message(...)`.
3. A re-drive of `share_internal` under the same context with a different preprocess set `preprocesses_B` (or different `key_pair`) rebuilds the machine from the same undeleted seed `s` — identical `d, e` — and emits `s_2 = d + b_B·e + λ_B·σ·c_B`.
4. Observer computes `b_A, b_B` (hash of preprocess commitments per FROST binding), `c_A, c_B` (transcript challenge), and `λ_A, λ_B` (Lagrange coefficients over `included`), then solves the two-equation system for `d, e, σ`, recovering the validator's secret share from public tributary data alone.

Reference points: seed-derived nonces (crypto/frost/src/sign.rs:127-133), one-use mandate (sign.rs:216-219), undeleted cache read (coordinator/src/tributary/signing_protocol.rs:137-145), repeated `share_internal` invocations (signing_protocol.rs:156, 321-322).