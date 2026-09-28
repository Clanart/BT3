### Title
`SigningProtocol` reuses a cached FROST/MuSig nonce across `share()` and `complete()` without binding it to the signed inputs — ([File: coordinator/src/tributary/signing_protocol.rs](https://github.com/blackvul/serai--002/blob/main/coordinator/src/tributary/signing_protocol.rs))

### Summary
`SigningProtocol::preprocess_internal` caches a FROST preprocess seed keyed only by `self.context` (for `DkgConfirmer`, `(b"DkgConfirmer", attempt)`). Every call — `preprocess()`, `share()`, and `complete()` — reloads the *same* seed and rebuilds a `AlgorithmSignMachine` with identical nonces. `complete()` internally calls `share_internal` again, so `sign()` is executed at least twice under the same nonce. The cached seed is never consumed or marked spent, and nothing verifies that the `preprocesses`/`key_pair` fed to the second `sign()` are byte-identical to the first. Any divergence produces two distinct Schnorr signature shares on one nonce, enabling recovery of the validator's MuSig secret key.

### Finding Description
The bug class is "a resource committed to an earlier allocation is silently re-assigned to a new one": here the scarce resource is a deterministic nonce seed stored in `CachedPreprocesses`, indexed only by `context` (`signing_protocol.rs:88,123-145`). The seed fully determines the nonces via `ChaCha20Rng::from_seed` in `seeded_preprocess` (`crypto/frost/src/sign.rs:127-133`), and `share_internal` (`signing_protocol.rs:150-181`) calls `machine.sign(preprocesses, msg)` on every invocation.

Two `sign` executions occur per confirmation:

1. `DkgConfirmer::share()` → `share_internal` (`signing_protocol.rs:288-301`), emitting a share published on-chain.
2. `DkgConfirmer::complete()` → `share_internal` again (`signing_protocol.rs:312-326`), regenerating the machine and producing a second share before completing.

Both calls derive `msg` from `set_keys_message(set, removed, key_pair)` and binding factors `ρ` from the submitted `preprocesses` (`crypto/frost/src/sign.rs:361-371`). Neither the `key_pair` nor the preprocess set is bound into the cache context — only `attempt` is. If the second execution sees different inputs (e.g., a different `key_pair`, a different accumulated preprocess subset, or a different `removed` set returned by `removed_as_of_dkg_attempt`), the same `(d, e)` nonce pair is signed under a different challenge/binding factor. The file's own header admits safety depends entirely on inputs being identical across re-executions, yet the code never enforces or checks that.

### Impact Explanation
Recovery of the coordinator validator's MuSig secret key (the validator's root-of-trust key), which then permits forging signatures on arbitrary validator-set messages — full compromise of that validator's signing identity. With identical nonces and differing challenges `c1 ≠ c2`, `x = (s1 − s2) / (a·(c1 − c2))`; with differing preprocess sets (attacker-influenced `ρ`), a small number of shares suffices for ROS-style extraction.

### Likelihood Explanation
Re-execution with divergent inputs is a designed-for scenario: the module explicitly "uses a combination of the DB and re-execution" (`signing_protocol.rs:16`) and preprocesses/key_pair are re-read from on-chain accumulated data at share and completion time, not snapshotted. Any discrepancy between the data observed at `share()` vs `complete()` (different accumulated preprocess subsets, a superseded `key_pair`, changed removal set) triggers the leak — and the code has no guard detecting it.

### Recommendation
Consume the cached preprocess on first use of `sign()` (delete `CachedPreprocesses[context]` in `share_internal`), or bind the context to a hash of `(preprocesses, msg)` and abort if a `sign` is requested under an already-used context with differing inputs. At minimum, persist the published share and, on re-execution, re-emit it instead of re-signing.

### Proof of Concept
1. `DkgConfirmer::share()` runs `share_internal` with preprocess set P1 and `key_pair` K → publishes share `s1 = d + ρ1·e + c1·a·x` (context `(b"DkgConfirmer", n)` keeps the seed).
2. `DkgConfirmer::complete()` is invoked with a differing accumulated preprocess map P2 or a different `key_pair` (K′) → `share_internal` reloads the *same* seed, recomputes identical `(d, e)` (`sign.rs:127-133`), and signs `msg′ ≠ msg` → `s2 = d + ρ′·e + c2·a·x`.
3. With ρ1 = ρ′ (same preprocesses, different msg), `x = (s1 − s2) / (a·(c1 − c2))`, recovering the validator's MuSig private key.

Relevant code: `CachedPreprocesses` DB key and reuse at `coordinator/src/tributary/signing_protocol.rs:88,123-145`; double `sign` via `share_internal` at lines 150-181 and 312-326; nonce regeneration at `crypto/frost/src/sign.rs:121-144`; binding-factor/challenge construction at `crypto/frost/src/sign.rs:361-398`.