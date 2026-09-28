### Title
Cached FROST preprocess is reused across distinct messages/participant sets, enabling nonce reuse and secret share recovery - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
Analogous to the DyadStablecoin finding — where a deposit's USD value is computed from a *fresh* oracle price while share issuance uses a *stale* cached `totalDeposits`, letting a user combine the two out-of-sync values for profit — Serai's tributary signing protocol combines a *stale* cached FROST nonce seed with *fresh* attacker-influenced signing inputs. `CachedPreprocesses` is keyed only by `(b"DkgConfirmer", attempt)` and is never invalidated, so every call to `share()`/`complete()` re-derives the identical nonces while the binding factors, challenge, and message can differ. Two shares produced under the same nonces with different challenges leak the validator's MuSig/FROST secret share — the same "fresh input × stale fixed value = extraction" shape as deposit-then-rebase-then-redeem.

### Finding Description
`SigningProtocol::preprocess_internal` stores a ChaCha20 seed in the DB under `CachedPreprocesses: (context) -> [u8; 32]` and regenerates the deterministic preprocess via `AlgorithmSignMachine::from_cache` on every call. `seeded_preprocess` in `crypto/frost/src/sign.rs` (lines 121–144) derives `nonces` and `commitments` purely from that seed, so the nonce pair `(d, e)` — and hence the published commitments `(D, E)` — are identical for every signing attempt under the same context.

The context for `DkgConfirmer` is `(b"DkgConfirmer", self.attempt)` (line 275). Both `share()` (line 304) and `complete()` (line 312) funnel into `share_internal` → `preprocess_internal`, and `complete()` itself re-runs `share_internal` (line 322). The cache entry is never deleted or rotated, so a node that produces a share and later re-executes `share`/`complete` with *different* inputs reuses the same `(d, e)` while:

- the effective nonce `d + rho·e` depends on `rho`, the binding factor over each participant's commitments, `group_key`, and `hash_msg` — all of which change if any other participant's preprocess bytes differ;
- the challenge and the signed `msg` (`set_keys_message`, built from `key_pair`, line 296) may differ between invocations;
- the Lagrange coefficient `lambda` depends on `included`, which changes if the participant set differs.

A FROST share has the form `s = d + rho·e + lambda·x·c`. Two shares `s1, s2` emitted with the same `(d, e)` but different `(rho, c, lambda)` give a linear system that an observer solves for the secret share `x`. Because the shares are published on-chain, any unprivileged observer who can cause (or simply witness) a second signing pass with divergent inputs — e.g., a different `KeyPair` in a retried confirmation, or a different subset of validator preprocesses reaching `complete` — recovers the validator's signing key.

The file's own header (lines 25–54) acknowledges "it is explicitly unsafe to reuse nonces" and rests safety entirely on BFT finality guaranteeing identical inputs; yet the code persists the seed across calls and re-executes `sign` unconditionally, with no check that the reconstructed `preprocess` matches what was previously published (explicitly noted as TODO at line 51), and no guard preventing `complete()` from re-signing under a different preprocess map than `share()` used.

### Impact Explanation
Recovery of a validator's MuSig key share. Since this protocol is used to confirm DKG results on-chain with the validators' root-of-trust keys, compromising a share contributes toward threshold compromise of the validator set key — the analog of "breaks the vault accounting and drains the vault," here "breaks the nonce invariant and extracts the key."

### Likelihood Explanation
Exploitation requires the same `(context, attempt)` to be signed twice with different `preprocesses`/`key_pair` inputs — e.g., a `complete` call whose preprocess map differs from the earlier `share` call, or a re-executed tributary block where a validator submits different preprocess bytes. It does not require breaking BFT, colluding, or compromising a node; it requires only that divergent signed inputs reach the signing functions, which the code does not defend against (the matching-on-chain check is an unimplemented TODO). Medium likelihood.

### Recommendation
- After producing a share, delete or tombstone the `CachedPreprocesses` entry so `share`/`complete` cannot re-sign under the same seed.
- Before emitting a share, verify the locally derived `preprocess` equals the preprocess bytes already published on-chain for this context (the TODO at line 51), and refuse to sign if inputs differ from the first execution.
- Persist the exact `(preprocesses, msg)` inputs alongside the cached seed and return the previously computed share on re-execution rather than re-running `sign`.

### Proof of Concept
1. `DkgConfirmer::share(preprocesses_A, key_pair_A)` executes → cache exists, seed reused → nonce `(d, e)`, share `s1 = d + rho1·e + lambda1·x·c1` published.
2. `DkgConfirmer::complete(preprocesses_B, key_pair_B, shares)` re-enters `share_internal` → same seed → same `(d, e)` → share `s2 = d + rho2·e + lambda2·x·c2` with `rho2 ≠ rho1` or `c2 ≠ c2` (different participant preprocesses and/or different `key_pair`).
3. Observer solves the two-equation linear system over `Ristretto::F` for `(d + ...)` and `x`, recovering the validator's secret share — no privileged access required, only the two public share broadcasts.