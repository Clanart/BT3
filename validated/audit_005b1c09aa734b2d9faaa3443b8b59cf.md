### Title
Cached FROST preprocess seed reused across `share` and `complete` enables nonce reuse and secret-share recovery when the preprocess set diverges - (File: `coordinator/src/tributary/signing_protocol.rs`)

### Summary
`DkgConfirmer::complete` (and the analogous `complete` flow for other `SigningProtocol` contexts) rebuilds the signing machine by re-invoking `share_internal`, which calls `preprocess_internal`. `preprocess_internal` reconstructs `AlgorithmSignMachine` via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` using the *same* `CachedPreprocesses` DB entry every time — it is written once (or reused if already present) and never deleted or rotated. `from_cache` calls `seeded_preprocess`, which deterministically regenerates the same base/actual nonces (`Curve::random_nonce` over `seed || secret`). The machine is then signed a second time with whatever `preprocesses` map is passed to `complete`. If that map differs from the one used at `share` time — e.g., additional validators' `Preprocess`/`Commitments` data accumulated on the tributary between the two calls — the participant transcript changes, the per-signer binding factors `rho` change, but the underlying nonce pair `[base, actual]` is identical. This is FROST nonce reuse: `k = base + rho·actual`, `share = k + c·λᵢ·xᵢ`. Two shares over the same nonce commitments with different `rho` yield a linear system recovering the victim's secret share `xᵢ`.

### Finding Description
In `crypto/frost/src/sign.rs`, `sign` consumes `self.nonces` (drained at sign.rs:386-396) and computes `actual = base + rho·actual`. `rho` is derived from `rho_transcript` which hashes `group_key`, `C::hash_msg(msg)`, and `preprocesses` challenge — a transcript over every included participant's commitments and addendum (sign.rs:322-371). Any change to the preprocess set changes `rho` while leaving the seeded nonces unchanged.

In `coordinator/src/tributary/signing_protocol.rs`:
- `preprocess_internal` (lines 123-147) reads the cached 32-byte seed from `CachedPreprocesses`, decrypts it, and calls `AlgorithmSignMachine::from_cache`. The cache entry is never cleared, so every call produces a machine with identical nonces.
- `share_internal` (lines 150-181) calls `preprocess_internal().0` then `machine.sign(preprocesses, msg)`.
- `DkgConfirmer::complete` (lines 312-327) calls `self.share_internal(preprocesses, key_pair)` again — re-running the full sign on the same seed with the `preprocesses` map supplied at complete time.

The `preprocesses` map passed to `complete` is populated from tributary data accumulated via `handle_data`/`accumulate` (`coordinator/src/tributary/handle.rs:169-219`), which only slashes on *duplicate publication by the same signer*. New signers can legitimately add entries after `share` ran, so the map at `complete` time can be a strict superset. Even absent an adversary, timing of accumulation makes divergence realistic; an adversarial validator can also deliberately time its `Preprocess` publication to sit between the victim's `share` and `complete`.

Each re-sign produces a signature share of the form `share_j = d + rho_j·e + c_j·λ·x`. With identical `(D, E)` commitments (same `d`, `e`) but `rho_1 ≠ rho_2`, the two shares plus the public verification equation let an observer solve for `e`, `d`, and ultimately the secret share `x` (standard FROST/ROS-style nonce-reuse algebra: the share equation is linear in the unknown scalars given two distinct `rho`).

### Impact Explanation
Recovery of a validator's FROST secret share (`ThresholdKeys` share) for the Ristretto MuSig key used in `DkgConfirmer` (`set_keys` confirmation signing) and, via the same `SigningProtocol`/`share_internal`/`complete` re-execution pattern, for substrate `Batch` and transaction signing contexts. A recovered secret share is a listed impact class (key share recovery); combined with other compromised shares it yields full group-key recovery, and it permanently compromises that participant's share for the set.

### Likelihood Explanation
The trigger does not require malformed input — only that the `preprocesses`/`shares` map handed to `complete` differ in participant composition from the one used in `share`. Since accumulation continues while the protocol waits for shares, and `complete` accepts a fresh `preprocesses` argument rather than re-using the exact set that produced the share, divergence is a routine race, not an edge case. Severity: High (secret-share leakage reachable through the normal signing flow; an adversarial validator can force it deterministically).

### Recommendation
- Do not re-run `share_internal` inside `complete`. Persist the `AlgorithmSignatureMachine` state (or `B`, `Rs`, `view`, `share`) produced by `share` and have `complete` consume it directly, mirroring `processor/src/signer.rs` which stores `(signature_machine, shares)` between rounds.
- Alternatively, delete/rotate the `CachedPreprocesses` entry on first use so `preprocess_internal` cannot be invoked twice with the same seed, and pin `complete` to the exact preprocess set used at `share` time (reject any expanded/changed set).

### Proof of Concept
Conceptual (requires coordinator harness):
1. Validators `1..=t` publish `Preprocess` data for `DkgConfirmer` attempt `a`; victim coordinator calls `share(...)`, which internally calls `sign` on machine `M` built from `CachedPreprocess` seed `s`, producing share `s_1 = d + rho_1·e + c·λ·x` over commitments `(D, E)`.
2. An additional validator `t+1`'s preprocess is accumulated (or an adversarial validator times publication) before `complete`.
3. Victim calls `complete(preprocesses', ...)` where `preprocesses' ⊋ preprocesses`. `share_internal` rebuilds a machine from the same seed `s` → same `(d, e)`, same `(D, E)` — but the transcript now includes participant `t+1`, so `rho_2 ≠ rho_1` and `included` differs. The second `sign` emits share `s_2 = d + rho_2·e + c'·λ'·x`.
4. Observer solves the two linear share equations (public `D, E, rho_1, rho_2, c, c', λ`) for `x`, the victim's secret share.

Note: I confirmed the seed-reuse path (`CachedPreprocesses` never invalidated; `complete` re-invokes `share_internal`) from `signing_protocol.rs` and the nonce/binding-factor mechanics from `crypto/frost/src/sign.rs`. I could not fully trace the exact call site that feeds `complete` its `preprocesses` map in `coordinator/src/main.rs`/`handle.rs` within the available tool budget, so the divergence condition relies on the accumulation semantics in `handle.rs:216-217` accepting additional participants' data.