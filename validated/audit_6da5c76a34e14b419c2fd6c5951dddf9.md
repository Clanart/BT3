### Title
Deterministic `CachedPreprocess` is reused across distinct `sign()` calls under the same context, reusing FROST nonces and enabling secret-share recovery - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
The kernel bug is a missing "is this object live?" guard: `vxlan_changelink()` arms `age_timer` on a device that is down, and the only cancel path (`ndo_stop`) is skipped for never-up devices, leaving live state attached to a dead object. The Serai analog is a missing "has this preprocess already been consumed?" guard: `preprocess_internal` persists a deterministic `CachedPreprocess` seed per `context` and `share_internal` regenerates a signing machine from that same seed on every call, so multiple `sign()` invocations under one context reuse the exact same FROST nonces `d` and `e`.

### Finding Description
`SigningProtocol::preprocess_internal` computes a `CachedPreprocess` once per `self.context` and stores it (XOR-encrypted) in `CachedPreprocesses` (coordinator/src/tributary/signing_protocol.rs:123-134). On every subsequent call it decrypts the *same* 32-byte seed and calls `AlgorithmSignMachine::from_cache` → `AlgorithmMachine::seeded_preprocess` (lines 137-145, crypto/frost/src/sign.rs:268-273). FROST nonces are drawn from the RNG as `C::random_nonce(secret_share, &mut rng)` (crypto/frost/src/nonce.rs:58-60); with a seeded (deterministic) RNG derived solely from the cached 32-byte seed, the resulting `Nonce([d, e])` pair — and therefore the published `Commitments` — are identical on every reuse.

`share_internal` then feeds that regenerated machine straight into `machine.sign(preprocesses, msg)` with whatever `msg` the caller supplies (lines 156-170). `AlgorithmSignMachine::sign` consumes `self.nonces` with no check that the cached seed has already produced a share (crypto/frost/src/sign.rs:283-313). There is no "already armed/used" flag on the cached seed — the exact missing `netif_running()`-style state guard from the kernel bug: the timer (nonce state) is re-armed on an object whose lifecycle doesn't cancel it (the seed is deliberately persisted and never invalidated after a successful `sign`).

If two distinct `msg` values (or even the same `msg` with a different `included` set, producing different binding factors `rho` and challenge `c`) are signed under the same `context`, the attacker obtains two shares `s1 = r + c1·λ·x` and `s2 = r + c2·λ·x` over the identical nonce `r = d + ρ·e`, yielding `x = (s1 − s2) / (λ·(c1 − c2))` — full secret-share recovery of the Ristretto threshold key backing tributary signing.

### Impact Explanation
Recovery of the validator's `ThresholdKeys<Ristretto>` secret share (`self.key`, line 93). Shares are broadcast to peers (line 180 returns the serialized share), so any party observing two shares over identical nonce commitments — trivially detectable since the serialized preprocess `D, E` points are byte-identical — can extract the share. Combined with other shares or used alone, this undermines the threshold signing key protecting Serai's substrate-side operations. Impact class: key share recovery.

### Likelihood Explanation
Reachability requires the coordinator to call `share_internal` more than once under a single `context` with differing signing inputs. The cache is keyed only by `context` (`CachedPreprocesses::get(self.txn, &self.context)`), and `share_internal` calls `preprocess_internal` unconditionally at line 156 for every signing round — there is no per-`msg`, per-attempt, or consumed-flag in the key. Whether a single `context` legitimately signs multiple messages depends on how the coordinator constructs `C` for each Tributary sign operation; I could not fully verify that within the available context (the construction site of `SigningProtocol` and the cardinality of `context` per message were not inspected). If `context` is per-signing-session rather than per-message, reuse — and share recovery — occurs whenever the protocol re-attempts or signs multiple payloads in that session. The nonces are also attacker-influenceable indirectly: any change to the `preprocesses` map or `msg` changes `rho`/`c` while `r` stays fixed.

### Recommendation
Bind each cached preprocess to a single use:
- Key `CachedPreprocesses` by `(context, attempt/nonce-counter)` rather than `context` alone, and delete or tombstone the entry after `share_internal` produces a share.
- Alternatively, mix the `msg` and the sorted `included` participant set into `seeded_preprocess`'s RNG seed so distinct signing runs cannot produce identical nonces.
- At the `frost` layer, have `AlgorithmSignMachine::sign` refuse to run twice from the same `CachedPreprocess` (e.g., embed a consumed flag or a monotonic counter in the cache).

### Proof of Concept
1. Instantiate `SigningProtocol` with a fixed `context`.
2. Call `share_internal(participants, preprocesses_a, msg_a)` → publishes `preprocess` bytes `P` and share `s1`.
3. Call `share_internal(participants, preprocesses_b, msg_b)` (or same `msg` with a different participant subset) → publishes the *same* `P` (deterministic `seeded_preprocess`) and share `s2`.
4. Since `s_i = (d + ρ_i·e) + c_i·λ·x` and `P` identical ⟹ same `d, e`: solve the two-equation system for `x` after deriving `ρ_i` from the public `rho_transcript` (crypto/frost/src/sign.rs:361-371) and `λ` from `ThresholdView`.
5. Recovered `x` is the node's Ristretto secret share for `musig(musig_context(spec.set()), key, participants)`.