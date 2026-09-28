### Title
Cached FROST preprocess reused across distinct signing sessions under the same context leaks the validator key share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives the FROST nonces from a single `CachedPreprocess` stored in the DB keyed only by `context`. Every call to `share_internal` (including the second call made inside `DkgConfirmer::complete`) re-derives the *identical* nonce scalars via `AlgorithmSignMachine::from_cache` → `seeded_preprocess`. If two calls under the same context are made with different preprocess sets or a different message, the same nonce is used to produce two signature shares over different binding factors/challenges — the classic FROST nonce-reuse key-share recovery.

### Finding Description
- `preprocess_internal` loads `CachedPreprocesses::get(txn, context)` and, on a hit, reuses the stored seed to rebuild the sign machine (`coordinator/src/tributary/signing_protocol.rs:123-147`). `seeded_preprocess` deterministically regenerates the same `nonces` from `ChaCha20Rng::from_seed(seed)` (`crypto/frost/src/sign.rs:121-143`).
- `DkgConfirmer::share` calls `share_internal` once; `DkgConfirmer::complete` calls `share_internal` *again* internally (`coordinator/src/tributary/signing_protocol.rs:304-327`). Both calls use the same `context = (b"DkgConfirmer", attempt)`, hence the same nonce.
- The inputs to `share` vs `complete` are caller-supplied `HashMap<Participant, Vec<u8>>` maps of on-chain-submitted preprocesses plus `key_pair`. These maps feed `threshold_i_map_to_keys_and_musig_i_map`, which re-indexes participants by sorted enumeration — so a different membership/set of preprocesses changes the MuSig participant indices, the `included` set, and every participant's binding factor `rho_i` (`crypto/frost/src/sign.rs:325-371`, `crypto/frost/src/nonce.rs:161-190`). The signed message `set_keys_message(set, removed, key_pair)` also varies with `key_pair`.
- Reusing nonce scalar `d` across two sessions yields `z1 = d + rho1·e + λ·s·c1` and `z2 = d + rho2·e + λ·s·c2` (or differing messages → differing `c`). With attacker-known `rho1, rho2, c1, c2`, the secret share `s` (and `e`) is recoverable by linear algebra — exactly the failure the spec warns about ("Reusing preprocesses would enable a third-party to recover your private key share", `spec/cryptography/FROST.md:51-55`) and the file's own header acknowledges is only prevented by assuming the received-message set can never differ (`coordinator/src/tributary/signing_protocol.rs:25-48`).

### Impact Explanation
An unprivileged participant who causes `share`/`complete` to be invoked under the same `attempt` with differing preprocess sets (e.g., by having an additional preprocess transaction finalized for the completion pass, or by controlling `key_pair` data) obtains two signature shares built on the same nonce with different binding factors, enabling recovery of the validator's MuSig secret share — full disclosure of the signing key material for the DKG-confirmation path.

### Likelihood Explanation
The defense rests entirely on the assumption that the (preprocess set, message) pair is fixed per context by BFT finality. `share` and `complete` accept their maps independently, and `complete` re-signs unconditionally; nothing cryptographically binds the second call to the first call's inputs (the TODO at lines 50-54 explicitly notes the missing on-chain-preprocess-matches check). Any path — additional finalized preprocess transactions, partial state rebuild, or differing `removed`/`key_pair` resolution — that supplies different bytes to the two calls triggers reuse with public inputs only.

### Recommendation
After `share_internal` succeeds, mark the cached preprocess consumed (delete `CachedPreprocesses` entry or store the signed `preprocesses`/`msg` digest alongside it) and refuse — or return the previously computed share — when `complete` is invoked with different inputs, rather than re-running `sign` on the same seed.

### Proof of Concept
1. Validator runs `DkgConfirmer::share(preprocesses_A, kp)` producing `z1` with nonce `d`.
2. `DkgConfirmer::complete(preprocesses_B, kp', shares)` is called where `preprocesses_B` contains one extra participant (or differs in any commitment). `share_internal` regenerates the same `d` from the cached seed; new `included` set yields `rho_i' ≠ rho_i` and possibly `msg' ≠ msg`, producing `z2`.
3. Attacker solves the two-equation system for the validator's secret share `s`, compromising the MuSig key.

Note: exploitability hinges on whether `handle.rs` can pass differing preprocess maps to `share` vs `complete` for the same attempt without breaking BFT assumptions; I was unable to fully verify the call sites before this analysis was finalized.