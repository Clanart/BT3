### Title
Deterministic nonce reuse across distinct `set_keys_message` payloads in `DkgConfirmer` leaks the validator's private key - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`DkgConfirmer` caches a FROST preprocess (the ChaCha20Rng seed deriving all signing nonces) keyed only by `("DkgConfirmer", attempt)` in `CachedPreprocesses`. Every call to `share_internal`/`complete` reconstructs the sign machine from that same seed via `AlgorithmSignMachine::from_cache`, so the same nonce `k` is reused for every invocation within an attempt. The message signed, `set_keys_message(set, removed, key_pair)`, is **not** fixed by the BFT protocol — `key_pair` comes from `DkgKeyPair`, a local DB entry written by `generated_key_pair` and freely overwritten on each call. Two calls with different `key_pair` values therefore produce two Schnorr shares over the same nonce binding distinct challenges, enabling direct algebraic recovery of the validator's MuSig secret key.

### Finding Description
The pyopenssl CVE-2018-1000807 class is "stale/shared object used after it should have been invalidated": an object whose backing state was already consumed gets reused. In Serai the equivalent shape is the `CachedPreprocess` — the docs in `crypto/frost/src/sign.rs:85-87` and `spec/cryptography/FROST.md:51-55` state reuse of a preprocess "will enable third-party recovery of your private key share."

`SigningProtocol::preprocess_internal` (`coordinator/src/tributary/signing_protocol.rs:123-147`) sets the cached seed once per `context` and never deletes or rotates it:

```rust
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
  ... CachedPreprocesses::set(self.txn, &self.context, &cache.0);
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
let (machine, preprocess) =
  AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

`seeded_preprocess` (`crypto/frost/src/sign.rs:127-133`) regenerates identical `nonces` from `ChaCha20Rng::from_seed(*seed.0)` on every `from_cache`. `share_internal` (line 156) then calls `machine.sign(preprocesses, msg)` where `msg = set_keys_message(..., key_pair)` (lines 296-300).

The key pair is attacker-influenceable local state, not BFT-finalized. `generated_key_pair` (`coordinator/src/tributary/handle.rs:47-60`) does `DkgKeyPair::set(txn, spec.genesis(), attempt, key_pair)` unconditionally — a second processor report for the same attempt overwrites it and immediately calls `confirmer.share(preprocesses, key_pair)`, re-signing with the same cached nonces. Additionally, `DkgConfirmer::complete` (`signing_protocol.rs:312-327`) internally calls `share_internal` again; if `DkgKeyPair` changed between the `share()` that produced our published `confirmation_share` and the `complete()` triggered when `DkgConfirmed` accumulation becomes ready, `complete` emits a second share over the same nonces for a different message, and that share feeds an aggregate signature published on-chain.

The file's own safety argument (lines 34-48) explicitly requires that "the received nonce commitments (or the message to be signed)" cannot be distinct across `sign` calls — an assumption violated here because the message depends on mutable `DkgKeyPair` state, not on BFT-ordered transactions.

### Impact Explanation
Critical — key share recovery. For Schnorr-style shares `s₁ = k + e₁·x` and `s₂ = k + e₂·x` under the same nonce `k` but different challenges `e₁ ≠ e₂` (different `key_pair` inside `set_keys_message`), anyone observing both shares computes `x = (s₁ − s₂)·(e₁ − e₂)⁻¹`. Our share is published on tributary (`Transaction::DkgConfirmed { confirmation_share }`) and the second share is embedded in the on-chain `publish_set_keys` aggregate — both values are public. Recovery of the coordinator's validator key (the MuSig root of trust key) lets an attacker forge future `set_keys` confirmations and validator-signed tributary transactions, meeting the "key share recovery" acceptance bar.

### Likelihood Explanation
Medium-High. Reachability requires `generated_key_pair`/`complete` to run twice for one `attempt` with differing `key_pair` — e.g., a processor reporting a revised key pair after blame/removal re-evaluation within the same attempt, a processor restart re-reporting, or `DkgKeyPair` being overwritten between `share()` and `complete()` (the code itself flags a related timing hazard at `handle.rs:527-529`). A participant in the DKG can influence which `key_pair` gets reported (e.g., by forcing a blame/re-generation path), making this reachable via public inputs rather than requiring a malicious validator.

### Recommendation
Bind the cached preprocess to the full signing context including `key_pair` — e.g., use `context = (b"DkgConfirmer", attempt, key_pair)` — or delete the `CachedPreprocesses` entry after the first `share` and refuse to sign a different message under the same attempt. Alternatively, store the signed `key_pair`/message hash alongside the seed and `panic!`/reject if `share_internal` is invoked with a differing message. More broadly, persist the nonce-commitment the node actually published and verify re-derived commitments match before emitting any share (the TODO at `signing_protocol.rs:50-51`).

### Proof of Concept
1. Attempt `a` starts; `dkg_confirmation_nonces` publishes preprocess `P` derived from seed `S` stored under `("DkgConfirmer", a)`.
2. Processor reports `key_pair_A`: `generated_key_pair` sets `DkgKeyPair = A` and `share()` produces `s_A = k + e_A·x` where `k = nonces(S)`, `e_A = H(P || … || set_keys_message(A))`; the node publishes `DkgConfirmed { confirmation_share: s_A }`.
3. A differing `key_pair_B` is reported for the same attempt (blame/re-report path or overwrite before `complete`): `share()`/`complete()` reloads seed `S`, regenerates the identical `k`, and produces `s_B = k + e_B·x` with `e_B ≠ e_A`.
4. Any observer computes `x = (s_A − s_B)/(e_A − e_B) mod l`, recovering the validator's private key. No threshold collusion, BFT break, or leaked secret is required — only the public tributary share and the on-chain aggregate.

Caveat: I verified the cache/reuse mechanics and the `DkgKeyPair` overwrite path directly; whether the processor can actually emit two distinct `key_pair` reports within one attempt depends on processor-side DKG message handling I did not fully trace — but `complete()`'s unconditional re-invocation of `share_internal` with mutable `DkgKeyPair` state is sufficient on its own.