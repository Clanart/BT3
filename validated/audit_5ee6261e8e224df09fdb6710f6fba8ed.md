### Title
Cached FROST preprocess seed is never invalidated — second `sign` under the same context reuses nonces, enabling key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` deterministically rebuilds the same FROST preprocess (same nonces) from a `CachedPreprocess` seed stored under `CachedPreprocesses` keyed by `context`, and never deletes or rotates it. `share_internal` — invoked by both `DkgConfirmer::share` and again inside `DkgConfirmer::complete` — calls `machine.sign(preprocesses, msg)` each time. If the `preprocesses` set (or `msg`) differs between two invocations under the same `(b"DkgConfirmer", attempt)` context, the same nonces are signed under two different binding factors/challenges, leaking the validator's secret key share — the exact analog of a reset credential not invalidated after use.

### Finding Description
- `preprocess_internal` stores a freshly generated `CachedPreprocess` (the ChaCha20 seed from which all nonces and commitments are derived) under `CachedPreprocesses` keyed by `self.context`, and on every subsequent call reloads the *same* seed via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` (`crypto/frost/src/sign.rs:121-144`). Nothing invalidates the cache after `sign` runs.
- The module documentation itself states reuse requires "sign [to] be called again" with "distinct received messages" (`signing_protocol.rs:34-35`), and flags as TODO the missing check that the commitments derived from the decided nonces match what is on-chain (`signing_protocol.rs:50-54`), plus a TODO to review Processor preprocess handling.
- `DkgConfirmer::share` calls `share_internal(preprocesses, key_pair)`, and `DkgConfirmer::complete` calls `share_internal` a second time (`signing_protocol.rs:321-324`). The `preprocesses` map is reconstructed per call from on-chain transaction data via `threshold_i_map_to_keys_and_musig_i_map`, so the signing set handed to `SignMachine::sign` is not pinned to the set used the first time. If a later `complete` (or a re-executed `share`) sees a different participant set or preprocess collection than the earlier call, `sign` executes again over the identical nonces.
- Because FROST shares are linear in the nonce (`s = nonce + challenge · share`), two published shares over the same nonce with different `rho`/challenge yield the secret share by algebra — the file itself notes "recovery of it will enable recovering the private key" (`signing_protocol.rs:104`).

### Impact Explanation
Recovery of a validator's MuSig/FROST secret key share. Since this protocol signs `set_keys_message` confirmations for DKG results, a recovered share undermines the threshold root-of-trust for validator-set key confirmation. The published shares are on-chain/public, so any observer can perform the recovery once two distinct shares over the same nonce exist.

### Likelihood Explanation
The safety argument relies entirely on inputs to `sign` being identical across re-executions. That invariant is not enforced: there is no check that the re-derived preprocess commitments match the originally published ones (explicitly a TODO), and the preprocess set fed to `complete` is independently rebuilt from transaction data, so a differing set of preprocesses (e.g., additional participants' preprocesses finalized between the `share` and `complete` handling, or a partial DB rebuild which the comments admit is possible) triggers a second `sign` with reused nonces. Requires no collusion beyond normal asynchronous preprocess arrival.

### Recommendation
- Delete or rotate `CachedPreprocesses[context]` immediately after the first successful `sign`, so any re-execution regenerates fresh nonces and produces a *different* preprocess that must be re-published.
- Implement the acknowledged TODO: before publishing a share, verify the preprocess commitments derived from the cached seed equal the commitments already published/finalized for this context; abort if they differ.
- Bind the exact signing set (sorted participant list) into the `context` key so a differing participant set can never reuse the same seed.

### Proof of Concept
1. Attempt `a` begins; `DkgConfirmer::preprocess()` loads/creates `CachedPreprocesses[(b"DkgConfirmer", a)] = seed` and publishes commitments `C = Commitments(seed)` on-chain.
2. A confirmation transaction containing preprocesses set `S1` is handled → `share(S1, kp)` → `sign` computes share `σ1` over nonce `k` (derived from `seed`) with binding factor `ρ1 = H(transcript, S1)`; `σ1` is published.
3. A later `complete` (or re-executed `share`) is invoked with preprocesses set `S2 ≠ S1` (different participant coverage finalized on-chain) → `share_internal` reloads the same `seed` → same nonce `k` → `sign` produces `σ2 = k' + c2·share` where `k'` uses the same underlying nonces but `ρ2 ≠ ρ1`.
4. With `σ1, σ2` public on-chain and `ρ1, ρ2` recomputable, solve for the nonce terms and then the secret key share `s` via `share = (σ1 − σ2 − Δnonce)/(c1 − c2)`-style elimination, recovering the validator's private key share.