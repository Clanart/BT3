### Title
Deterministic `CachedPreprocess` reuse in `share_internal` regenerates identical FROST nonces, enabling secret-share recovery via ROS-style nonce reuse - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary

The tributary signing protocol persists a FROST preprocess seed (`CachedPreprocess`) in the database keyed only by the signing `context`, and rebuilds the signing machine from that seed deterministically every time `share_internal` runs. Because `seeded_preprocess` seeds `ChaCha20Rng` directly from the cached 32-byte seed, every `sign` call for the same context derives the *same* nonce scalars. If a participant triggers a second `share_internal` execution for the same context with a different preprocess set (different binding factors / challenge), the victim emits two signature shares over the same nonces — classic FROST nonce reuse — which linearly reveals the victim's secret key share.

### Finding Description

`preprocess_internal` in `coordinator/src/tributary/signing_protocol.rs:100-148` does the following:

1. Derives a symmetric encryption key as `Blake2s256("Cached Preprocess Encryption Key" || context.encode() || secret_key)` and XORs the `CachedPreprocess` seed with it (`signing_protocol.rs:107-142`).
2. If `CachedPreprocesses::get(txn, &context)` already exists, it **reuses** the stored seed rather than generating a fresh one (`signing_protocol.rs:123-135`).
3. Reconstructs the machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` (`signing_protocol.rs:144-145`).

`from_cache` calls `AlgorithmMachine::seeded_preprocess`, which instantiates `ChaCha20Rng::from_seed(*seed.0)` and derives `nonces` and `commitments` purely deterministically from that seed and the key material (`crypto/frost/src/sign.rs:121-144`). There is no attempt counter, no deletion of `CachedPreprocesses` after use, and no mixing in of the message or the other participants' preprocesses.

`share_internal` (`signing_protocol.rs:150-181`) then calls `self.preprocess_internal(participants).0` — a fresh machine with **identical nonces** — and signs the attacker-influenced `serialized_preprocesses` map. A participant who can cause a second `share_internal` call for the same `context` (submitting a distinct preprocess set / a repeated sign message for the same signing session) receives a second share computed as `s' = k + λ_i · c' · x_i` with the **same** `k` but a different effective challenge `c'`, since the per-participant binding factors `rho` are computed over the transcript of all preprocesses (`crypto/frost/src/sign.rs:361-371`, `crypto/frost/src/nonce.rs:161-173`).

The library's own documentation acknowledges the consequence: `cache()`/`from_cache` warn that reuse "will presumably cause the signer to leak their secret share" (`crypto/frost/src/sign.rs:216-219`). The coordinator's persistence layer guarantees that reuse occurs on any repeated invocation for a context, since the seed is stored non-volatily and never invalidated.

### Impact Explanation

Two shares `s1 = k + λ·c1·x_i` and `s2 = k + λ·c2·x_i` with identical nonce `k` yield `x_i = (s1 - s2) / (λ·(c1 - c2))` — full recovery of the victim's FROST secret key share by anyone observing the two shares (which are published on the tributary). Recovered shares permanently reduce the threshold's security (each leaked share lowers the effective threshold by one), and share leakage enables targeted attacks on remaining signers. This is the exact nonce-reuse / ROS-class failure the `CachedPreprocess` design warns against, made reachable by deterministic regeneration.

### Likelihood Explanation

Requires a signing-set participant to cause `share_internal` to execute twice under one `context` (e.g., a second preprocess-collection message for the same sign). The `context` keying is per signing operation and does not incorporate per-invocation freshness, so any retry/duplicate-delivery path in the tributary `Transaction::Sign` handling reaches it. Deterministic reuse is guaranteed by the code — the only mitigating factor is whether higher-level protocol logic deduplicates repeated sign invocations for a context, which is not enforced at this layer.

### Recommendation

- Delete or rotate `CachedPreprocesses` for a context after `share_internal` consumes it (one-shot semantics), or include an attempt/round counter in the cache key so repeated invocations derive independent seeds.
- Alternatively, derive the signing nonces as `hash(seed || transcript_of_all_preprocesses)` so different preprocess sets force different nonces, making reuse cryptographically impossible rather than policy-dependent.

### Proof of Concept

```rust
// coordinator/src/tributary/signing_protocol.rs
// share_internal calls preprocess_internal again for every invocation:
let machine = self.preprocess_internal(participants).0;   // line 156

// preprocess_internal reuses the DB-cached seed when present:
if CachedPreprocesses::get(self.txn, &self.context).is_none() { /* create */ }
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap(); // reused
let (machine, _) = AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));

// crypto/frost/src/sign.rs:121-144 — fully deterministic:
let mut rng = ChaCha20Rng::from_seed(*seed.0);
let (nonces, commitments) = Commitments::new(&mut rng, params.keys.original_secret_share(), ...);
```

Attack sketch: a co-signer submits preprocess set A, receives share `s1`; submits a *distinct* preprocess set B for the same `context` (different `rho` binding factors → different challenge), receiving `s2` built from the same `k`. `x_i = (s1 − s2)·(λ·(c1 − c2))⁻¹` recovers the victim's secret share.

Note: I was able to fully verify the deterministic-reuse mechanism in code; whether a duplicate sign invocation for an identical `context` is reachable depends on the tributary transaction dispatch logic that calls `share_internal`, which I could not fully trace within this analysis. If that layer enforces strictly-once semantics per context, this finding degrades to a latent footgun rather than a live exploit.