### Title
Deterministic nonce reuse across `share` and `complete` re-executions enables validator key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches a single FROST preprocess seed per `context` in the DB and regenerates identical nonces on every call. `share_internal` rebuilds the `AlgorithmSignMachine` from that same cached seed each time it runs. Both `DkgConfirmer::share` and `DkgConfirmer::complete` invoke `share_internal` independently, each time with a caller-supplied map of preprocesses and a message. If the set of preprocesses (or the message) differs between the two executions — e.g., a different signer set included, or distinct preprocess bytes finalized for the same participant — the same binomial nonces `(d, e)` are signed under different binding factors `rho` and different challenges `c`, producing two shares that trivially yield the signer's secret share.

### Finding Description
`preprocess_internal` stores `machine.cache()` XOR-encrypted under `CachedPreprocesses` keyed only by `self.context` (`("DkgConfirmer", attempt)`). On every subsequent call it loads the same seed and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`, which re-derives the identical `Commitments`/nonces via `ChaCha20Rng::from_seed` (crypto/frost/src/sign.rs:121-144). The seed is never deleted or rotated after use.

`share_internal` (lines 150-181) calls `preprocess_internal`, then `machine.sign(preprocesses, msg)`. The FROST share is `s = d + e·rho + c·λ·x` where `rho = H1(group_key, hash_msg(msg), hash_commitments(preprocesses))` is computed in `sign` (crypto/frost/src/sign.rs:361-371) and `c = Hram(R, group_key, msg)` in `sign_share` (crypto/frost/src/algorithm.rs:208). `rho` and `c` both depend on the received preprocesses and message, which arrive per-call.

`DkgConfirmer::share` calls `share_internal` once (line 309). `DkgConfirmer::complete` calls `share_internal` **again** (line 322) to rebuild the `AlgorithmSignatureMachine`, using whatever preprocess map is supplied at that point. The file's own header (lines 25-48) admits the safety argument rests entirely on "the received nonce commitments (or the message)" being identical across re-executions — an assumption enforced by nothing in this code. Because `share` and `complete` are driven by independently provided `HashMap<Participant, Vec<u8>>` arguments mapped through `threshold_i_map_to_keys_and_musig_i_map`, a distinct signing set, a re-sent/re-encoded preprocess, or a different `key_pair` (which changes `set_keys_message`, hence `msg`) between the two calls produces two shares over the same `(d, e)`:

```
s1 = d + e·rho1 + c1·λ·x
s2 = d + e·rho2 + c2·λ·x
```

With `rho1 ≠ rho2`, solve the 2×2 linear system for `e`, then recover `x` — the validator's MuSig private key (`self.key`, used as the FROST secret share via `musig(...).into()` at line 119).

### Impact Explanation
Recovery of a validator's `Zeroizing<<Ristretto as Ciphersuite>::F>` key — the exact secret used to confirm DKG results on-chain (`set_keys_message`). Combined with the shares of a threshold of validators, an attacker can forge `KeyPair` confirmations for arbitrary/compromised DKG outputs. Per the `SignMachine::cache` docs (crypto/frost/src/sign.rs:209-214), preprocess reuse "enables recovery of your private key share." This is a private-key-disclosure analog of the reported cross-origin information leak: material scoped to one signing execution is deterministically leaked into another.

### Likelihood Explanation
Reachable by any party able to influence the tributary data fed to `share`/`complete` (the preprocess maps and `KeyPair` are external inputs flowing through `threshold_i_map_to_keys_and_musig_i_map`). A validator who publishes two different preprocess messages, or a coordinator restart/re-execution boundary that presents a different signer subset to `complete` than was present at `share`, triggers the two-signing execution. The code acknowledges the danger and relies on BFT finality guaranteeing identical inputs, but `share` and `complete` take separately supplied maps at separate call sites with no equality check — the defense is assumption, not mechanism. Exploitation requires the inputs to differ, so likelihood is moderate rather than certain; impact is critical when triggered.

### Recommendation
- Include a binding hash of the preprocess set and `msg` in the `CachedPreprocesses` key/context, or store alongside the seed a commitment (`hash_commitments(preprocesses) || hash_msg(msg)`) recorded at first `sign` and verified on every subsequent `share_internal` call; abort on mismatch rather than re-signing.
- Record `included` and `msg` in the DB when the first share is produced and `assert_eq!` them in `complete` before reconstructing the machine.
- Alternatively, delete/rotate the cached seed after the first `sign` (the documented "MUST only be used once" contract), and persist the produced `AlgorithmSignatureMachine` state needed for `complete` instead of re-deriving it via a second signing.

### Proof of Concept
1. `DkgConfirmer::new(key, spec, txn, attempt)` builds context `("DkgConfirmer", attempt)`.
2. Call `confirmer.share(preprocesses_A, key_pair)` → `share_internal` → `preprocess_internal` stores seed `S` and signs with nonces `(d, e)` under `rho_A`, `c_A`, emitting `s_A`.
3. Call `confirmer.complete(preprocesses_B, key_pair, shares)` where `preprocesses_B` differs from `preprocesses_A` (different participant subset or a second preprocess published by any validator) → `share_internal` reloads `S`, regenerates identical `(d, e)`, signs under `rho_B ≠ rho_A`, `c_B`, producing `s_B`.
4. Observe both shares from the emitted `[u8; 32]` values; compute `e = (s_A - s_B - (c_A - c_B)·λ·x̂)...` — concretely, `x = ((s_A - s_B) - e·(rho_A - rho_B)) / ((c_A - c_B)·λ)` after solving `d, e` from the two-equation system, recovering the validator's private key.

All primitives needed (`hash_msg`, `hash_commitments`, `BindingFactor`, `hram`) are deterministic public functions, so `rho_A`, `rho_B`, `c_A`, `c_B`, and `λ` are computable offline from public transcript data.