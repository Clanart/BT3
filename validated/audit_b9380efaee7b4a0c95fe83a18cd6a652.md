### Title
Cached DKG-confirmation nonces are keyed only by `(b"DkgConfirmer", attempt)`, allowing the same FROST preprocess/nonces to be reused across distinct validator sets on the same coordinator - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` deterministically derives a validator's FROST preprocess from a `CachedPreprocess` seed stored in `CachedPreprocesses`, keyed solely by `context` = `(b"DkgConfirmer", self.attempt)`. The validator set, tributary genesis, and network are *not* bound into the DB key or into the XOR "encryption key" (`"Cached Preprocess Encryption Key" || context.encode() || key`). If the coordinator runs this protocol for more than one validator set (distinct `TributarySpec`s) at the same attempt number — e.g., DKG attempt 0 for multiple networks/sessions — the same seed is loaded and `AlgorithmSignMachine::from_cache` regenerates identical nonce pairs `(d, e)` for signing different messages under different MuSig aggregation parameters. This is the analog of GHSA-837q-jhwx-cmpv: credentials (here, nonce commitments/shares) valid in one context are reused in another because the credential is not bound to the application/set scope.

### Finding Description
The FROST spec bundled with Serai states that preprocess reuse "would enable a third-party to recover your private key share" (`spec/cryptography/FROST.md:51-55`), and `CachedPreprocess` is documented as single-use (`crypto/frost/src/sign.rs:83-92`). `seeded_preprocess` deterministically derives both FROST nonces via `ChaCha20Rng::from_seed(*seed.0)` (`crypto/frost/src/sign.rs:127-133`), so seed reuse = nonce reuse.

In `preprocess_internal` (signing_protocol.rs:123-145), the cache lookup/set uses only `self.context`, which `DkgConfirmer::signing_protocol` constructs as `(b"DkgConfirmer", self.attempt)` (line 275). Nothing about `self.spec` — the validator set, genesis, or network — enters the key. The safety argument in the file header (lines 25-48) relies on BFT finality within *one* chain; it does not address the same coordinator's DB serving multiple sets/tributaries, where no BFT ordering relates distinct sets' messages.

### Impact Explanation
Each signing session produces a share of the form `s_i = d + e·ρ_i + c_i·λ_i·x_i`. With a reused `(d, e)`, every colliding session contributes one linear equation over the unknowns `(d, e, k)` (the validator's underlying key `k`; MuSig binding factors `b_i`, Lagrange coefficients `λ_i`, binding factors `ρ_i`, and challenges `c_i` are all publicly computable from the preprocesses, `set_keys_message`, and participant lists). Three colliding sessions (three sets at the same DKG attempt) yield a fully determined system, recovering the validator's private key — the root-of-trust key used to confirm DKG results on-chain.

### Likelihood Explanation
Requires the same coordinator DB to process DKG confirmations for multiple validator sets at the same `attempt` index. I could not fully verify (within available iterations) whether a single coordinator process/DB instance actually serves multiple tributaries with colliding attempt counters; `coordinator/src/tributary/handle.rs` contains the `DkgConfirmer` call sites and would confirm this. If confirmed, no malicious validator, broken BFT, or leaked key is needed — the collision is a pure indexing bug, and the attacker's inputs are the public preprocesses/shares.

### Recommendation
Include a set-scoped identifier in both the DB key and the XOR mask: e.g., `context = (b"DkgConfirmer", spec.genesis() or spec.set().encode(), self.attempt)` and `encryption_key_preimage.extend(spec.set().encode())`. Alternatively, key `CachedPreprocesses` by `(set, context)`.

### Proof of Concept
1. Coordinator runs `DkgConfirmer` for validator sets A, B, C, each at `attempt = 0`. Context for all three is `(b"DkgConfirmer", 0u32)`.
2. First call to `preprocess_internal` for set A stores `seed` under that context; calls for B and C load the same `seed` (line 137) and regenerate identical `nonces` via `from_cache` → `seeded_preprocess` (sign.rs:127-133).
3. Collect each session's public preprocesses and the validator's published shares `s_A, s_B, s_C` for their respective `set_keys_message`s.
4. Solve the linear system `s_i = d + e·ρ_i + c_i·λ_i·b_i·k` for `(d, e, k)` over the Ristretto scalar field, recovering the validator's MuSig private key `k`.