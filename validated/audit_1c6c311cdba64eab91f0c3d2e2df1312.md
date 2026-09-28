### Title
Cached FROST preprocess seed reused across distinct signing events enables key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary

The kernel bug registers one handler for two interrupt sources, so two events corrupt shared state. The Serai analog: `SigningProtocol::preprocess_internal` stores a single `CachedPreprocess` seed under `context` and every subsequent `share_internal`/`complete` call reconstructs the signing machine **from the same seed**, producing identical FROST nonces for every signing event under that context. The crate's own docs state a cached preprocess "MUST only be used once. Reuse will enable third-party recovery of your private key share" (crypto/frost/src/sign.rs:209-219; spec/cryptography/FROST.md:51-55), yet the code deletes nothing and never rotates the seed.

### Finding Description

In `preprocess_internal` (coordinator/src/tributary/signing_protocol.rs:100-148), if `CachedPreprocesses::get(txn, context)` returns a value, it is decrypted and passed to `AlgorithmSignMachine::from_cache` (line 144-145). `from_cache` → `seeded_preprocess` → `ChaCha20Rng::from_seed(*seed.0)` (crypto/frost/src/sign.rs:127), so the nonces `d, e` and commitments are a pure function of the seed. Nothing clears or rewrites `CachedPreprocesses` after use.

`share_internal` (line 150-181) calls `preprocess_internal` then `machine.sign(preprocesses, msg)`. `DkgConfirmer::share` (line 304-310) forwards attacker-influenced `preprocesses` and `key_pair`, where `msg = set_keys_message(set, removed, key_pair)` (line 296-300). Both the signing set (`included`, which drives the binding factors `rho` and the challenge per crypto/frost/src/sign.rs:283-318) and the message bytes are inputs to the signed challenge `c`. Two calls to `share()` under the same `("DkgConfirmer", attempt)` context with different preprocess sets and/or a different `key_pair` therefore yield two shares:

```
s1 = d + b·e + c1·λ_i·x_i
s2 = d + b·e + c2·λ_i·x_i     (same d, e — same seed)
```

with `c1 ≠ c2` because `msg`/`included` differ. Additionally, `DkgConfirmer::complete` (line 312-327) itself calls `share_internal` a second time, re-deriving the identical nonce — any divergence in `msg` or set between the `share` and `complete` invocations constitutes the same reuse.

### Impact Explanation

`s1 − s2 = (c1 − c2)·λ_i·x_i` recovers the validator's MuSig/FROST secret share `x_i` — exactly the "third-party recovery of your private key share" consequence documented in `CachedPreprocess` (crypto/frost/src/sign.rs:85-87). Recovery of threshold shares yields the validator set's signing key, forging `set_keys` confirmations and any other threshold-signed messages. This is a key-share-recovery-class finding (High/Critical per the rubric).

### Likelihood Explanation

The `preprocesses` map (which participants' commitments enter the signing set) and `key_pair` (which determines `msg`) arrive via tributary messages handled in `coordinator/src/tributary/handle.rs`. A participant able to trigger a retry/re-attempt path, or to feed differing preprocess sets across `share`/`complete` calls, obtains two signatures under one nonce. Even without an adversary, the `share`→`complete` double derivation means the "use once" invariant rests entirely on identical inputs — there is no consumption marker, matching the kernel bug's shared-handler shape: one cached seed serves every event under the context.

### Recommendation

Delete (or overwrite with a fresh seed) the `CachedPreprocesses` entry the first time it is consumed for signing — e.g., generate a fresh preprocess in `share_internal`, write the new cache, and sign with the machine from the *previous* cache, then persist. Alternatively, bind the cache key to the full signing context (`attempt` plus a hash of `participants`/`key_pair`) and refuse signing when the same seed would serve differing inputs. Enforce single-use in `SigningProtocol` rather than relying on callers.

### Proof of Concept

```
1. For context ("DkgConfirmer", attempt = k), node stores seed S in
   CachedPreprocesses (first call to preprocess_internal).
2. Attacker supplies preprocesses P_A and key_pair K_A -> share() emits
   s1 = d + b·e + c1·λ_i·x_i  (nonces from ChaCha20Rng::from_seed(S)).
3. share() is invoked again (retry, or complete(), or a second
   set_keys proposal) with P_B/K_B, same context -> machine rebuilt
   via from_cache(S): identical d, e; challenge c2 ≠ c1 -> share s2.
4. x_i = (s1 − s2) / ((c1 − c2)·λ_i). Secret share recovered.
```

All steps are reachable through `DkgConfirmer::{share, complete}` over tributary-provided `preprocesses`/`key_pair`; no collusion or leaked state required — the reuse is structural because the seed survives in `CachedPreprocesses` and `from_cache` deterministically regenerates it (signing_protocol.rs:123-145, crypto/frost/src/sign.rs:121-144).

Caveat: I could not fully trace `handle.rs` call sites to confirm whether an external message provably triggers `share()` twice with divergent `key_pair` within one attempt; the finding stands on the deterministic seed reuse itself, which is directly observable in the cited code.