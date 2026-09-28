### Title
FROST signing seed persisted indefinitely in the coordinator DB and reused on re-execution, enabling nonce reuse and validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to CVE-2020-15085 (sensitive authentication data persisted in local storage and surviving logout), the coordinator persists each FROST preprocess — a seed which `modular-frost` itself documents as equivalent to the private key share — into the `CachedPreprocesses` database table keyed only by `context`, and never deletes it after the signing session concludes. Any later code path that signs under the same `context` re-derives identical nonces from the cached seed, even if the participant set or message has changed, producing the classic nonce-reuse key-recovery condition.

### Finding Description
`SigningProtocol::preprocess_internal` generates a preprocess once per `context` and writes the 32-byte `ChaCha20Rng` seed to `CachedPreprocesses` (`coordinator/src/tributary/signing_protocol.rs:123-135`). On every subsequent call for the same context it reloads that seed and rebuilds the identical nonces via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` (`crypto/frost/src/sign.rs:121-145`).

The entry is never removed: there is no `CachedPreprocesses::del` anywhere. `share_internal` calls `preprocess_internal` again and then `machine.sign(preprocesses, msg)` (`signing_protocol.rs:156-170`), and `DkgConfirmer::complete` calls `share_internal` a second time (`signing_protocol.rs:321-326`). The FROST docs explicitly warn that reuse of a cached preprocess "will enable third-party recovery of your private key share" (`crypto/frost/src/sign.rs:85-87`, `spec/cryptography/FROST.md:51-55`).

The protection that exists is only an XOR of the seed with `Blake2s256("Cached Preprocess Encryption Key" || context || key)` (`signing_protocol.rs:107-143`) — i.e., the secret is stored long-term in a database the code comments admit "isn't a proper secret store" (`signing_protocol.rs:104-106`).

The danger is the persistence-across-executions aspect: `DkgConfirmer::share`/`complete` and the other `SigningProtocol` consumers derive `msg` from `set_keys_message(set, removed, key_pair)` and the `removed` list from `removed_as_of_dkg_attempt` at call time (`signing_protocol.rs:271`, `296-301`). If `share` and `complete` (or a rebuilt node's re-execution) observe different `removed` sets or a different `key_pair` under the same `(b"DkgConfirmer", attempt)` context, the same nonces are signed under two distinct challenges/messages. The module's own comments flag exactly this gap: the commitments generated from cached nonces are not checked against what was published on-chain ("TODO", `signing_protocol.rs:50-54`).

### Impact Explanation
Two signature shares produced with identical nonces under different challenges yield the signer's secret share via standard Schnorr nonce-reuse algebra (`s1 - s2 = (c1 - c2) * share`, or via the differing binding factor `rho`). Here the leaked secret is the validator's root-of-trust Ristretto key used by the MuSig-based `DkgConfirmer` protocol — shares are published in on-chain `DkgConfirmed` transactions, so any observer (fully unprivileged) can perform the recovery once two divergent shares exist. This is key-share/key recovery, a High-severity outcome in scope terms.

### Likelihood Explanation
Exploitation requires `preprocess_internal` to serve the same cached seed to two `sign` invocations whose messages or participant preprocess sets differ. Within a single attempt the BFT ordering makes this unlikely, but the seed persists forever while: (a) `removed` is re-evaluated per call and can change between `share` and `complete`; (b) the acknowledged TODO admits a rebuilt node can re-decide or re-use preprocesses inconsistent with on-chain state. Because persistence is permanent and unchecked, any such divergence — rather than a narrow race — triggers the leak. Medium likelihood.

### Recommendation
Delete `CachedPreprocesses` entries when the signing session for a context completes (analogous to clearing local storage on logout), and before signing, verify the commitments derived from the cached seed match the commitments actually published for that context — the check already noted as missing in the `signing_protocol.rs:50-54` TODO. Consider binding the stored seed to the exact participant set and message it was created for.

### Proof of Concept
Conceptual, from the code paths:

1. Attempt `a` of the DKG runs; `dkg_confirmation_nonces` → `DkgConfirmer::preprocess` → `preprocess_internal` stores seed `S` under `context = (b"DkgConfirmer", a)` and publishes commitments `C`.
2. `generated_key_pair` → `DkgConfirmer::share` → `share_internal` reloads `S`, producing share `s1` over `msg1 = set_keys_message(set, removed1, kp)`.
3. A validator removal is processed for attempt `a` before `DkgConfirmed` accumulates, so `removed_as_of_dkg_attempt` now returns `removed2 ≠ removed1`.
4. `handle.rs:533-535` calls `confirmer.complete(...)`, which re-enters `share_internal`: the same seed `S` (same nonces `d, e`) is signed over `msg2 = set_keys_message(set, removed2, kp)` and a different MuSig participant list, yielding share `s2` with challenge `c2 ≠ c1` and binding factor `rho2 ≠ rho1`.
5. Both `s1` and `s2` appear in `DkgConfirmed` transactions. With `s_i = d + e·rho_i·λ + c_i·share`, anyone solving the two-equation system recovers `share` — the validator's secret key — fulfilling nonce-reuse key recovery.

Uncertainty: whether a real flow produces `removed1 ≠ removed2` (or differing `key_pair`) between `share` and `complete` for the same attempt was not fully verified against `removed_as_of_dkg_attempt` semantics; the finding stands on the persistent, never-invalidated cache plus the code's own acknowledgment that reuse-across-rebuilds is unchecked.