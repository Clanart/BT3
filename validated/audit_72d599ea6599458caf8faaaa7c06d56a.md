### Title
FROST rho transcript omits the effective signed message, enabling cross-context nonce reuse and secret-share recovery - (File: crypto/frost/src/sign.rs)

### Summary
The external finding is about required artifacts not being declared/bound into the commitment that deployment relies on. The Serai analog: the FROST binding-factor (`rho`) transcript commits only to `C::hash_msg(msg)` — the raw `msg` argument — while the actual Schnorr challenge is later computed by `Algorithm::sign_share` over a *different*, algorithm-mangled message. For `Schnorrkel`, that effective message is `u32(context.len()) || context || msg` (crypto/schnorrkel/src/lib.rs:122-127). The context prefix is never committed by the `rho_transcript`, so two sessions with identical preprocesses and identical raw `msg` but different Schnorrkel contexts produce identical binding factors and identical aggregated nonce `R = d + be`, yet different challenges — leaking every signer's private share.

### Finding Description
In `AlgorithmSignMachine::sign`, the binding transcript is built as: [1](#0-0) 

`rho_transcript` commits to `group_key`, `C::hash_msg(msg)` (the *unmodified* `msg`), and the hash of all preprocesses. Each participant's `rho` is then derived purely from this transcript plus their participant index (crypto/frost/src/nonce.rs:161-173), and the per-signer nonce is `base + actual * rho` (crypto/frost/src/sign.rs:385-396).

Only afterwards does `sign_share` compute the Fiat-Shamir challenge. For `Schnorrkel`, `sign_share` passes `context_len || context || msg` to `Schnorr::sign_share`/`Hram` (crypto/schnorrkel/src/lib.rs:117-128), and the generic Schnorr algorithm computes `c = Hram(R, group_key, that_msg)` (crypto/frost/src/algorithm.rs:208-210).

Because `rho` binds `hash_msg(msg)` rather than the effective message actually fed to `hram`, the preprocess commitments and raw `msg` fully determine every participant's combined nonce scalar `d_i + rho_i * e_i`, independent of the algorithm-level context.

### Impact Explanation
If the same signing set signs the same raw `msg` with the same preprocesses under two different Schnorrkel contexts (or under Schnorrkel and a plain `Schnorr`/`IetfSchnorr` session, or any two algorithms whose `sign_share` mangles `msg` differently), then:

- `R` and each participant's `rho` are identical across both sessions,
- so each signer's effective nonce `d_i + rho_i·e_i` is identical,
- but the challenges `c1 ≠ c2` differ.

Each share is `s = (d + rho·e) + c·lagrange·share_i`. Subtracting the two shares cancels the nonce: `share_i = (s1 - s2) / ((c1 - c2)·lagrange_i)`. Recovery of `t` private shares reconstructs the threshold group secret — full key compromise from publicly broadcast `SignatureShare` values (crypto/frost/src/sign.rs:183-188, 409).

### Likelihood Explanation
An unprivileged participant can cause this by supplying identical preprocess bytes in two sessions and inducing the set to sign the same raw `msg` — e.g., the coordinator's `share_internal` path (coordinator/src/tributary/signing_protocol.rs:150-180) reads attacker-supplied serialized preprocesses and signs a supplied `msg`. It requires the same signer set to run the same `msg` under two distinct algorithm/context wrappings; feasible wherever Schnorrkel context differs (different `Schnorrkel::new(context)` domains) or where the same group key is reused across algorithm variants. Preprocess reuse across sessions is normally impossible for an honest signer, but the *victim* doesn't reuse preprocesses — the two sessions legitimately generate fresh preprocesses; the collision exists whenever the preprocess *sets and msg* coincide, which an attacker coordinating the preprocess exchange can arrange by replaying their own preprocess and proposing the same `msg` in both sessions, since `rho` does not distinguish the effective messages.

Actually, strictly: identical preprocess *sets* are required, meaning all participants must send the same commitments in both sessions. An attacker alone cannot force honest parties to reuse nonces — but `CachedPreprocess`/`from_cache` (crypto/frost/src/sign.rs:127-141, 268-274) deterministically regenerates the same commitments whenever the same seed is reused, and `signing_protocol.rs` caches preprocesses per `context` key (coordinator/src/tributary/signing_protocol.rs:123-147). If the same cached preprocess is served for two sign invocations that reach `sign` with the same participant set and same `msg` but a different algorithm context, the share-recovery equation applies directly to the honest signer. This matches the external report's shape: an undeclared input (the algorithm-mangled message) is omitted from the commitment, so a dependency needed for uniqueness is missing.

### Recommendation
Bind the effective message, not the raw `msg`, into `rho_transcript`. Concretely, extend the `Algorithm` trait so the algorithm can transform/commit to the message *before* `calculate_binding_factors` is invoked — e.g., add `Algorithm::bound_message(&self, msg: &[u8]) -> Vec<u8>` (or transcript the message through `algorithm.transcript()` prior to computing the `preprocesses` challenge) and use `C::hash_msg(&algorithm.bound_message(msg))` at crypto/frost/src/sign.rs:364. For `Schnorrkel`, `bound_message` would return `context_len || context || msg`, matching what `sign_share` feeds to `hram`. This makes `rho` — and hence every nonce — unique per effective signed message, closing the cross-context collision.

### Proof of Concept
1. Validator set runs `Schnorrkel::new(b"substrate")` and (same keys, e.g., a second deployment or a parallel algorithm instance) `Schnorrkel::new(b"other")`.
2. Attacker arranges two sign calls with identical `included` sets and identical `Preprocess` maps (feasible via the cached-preprocess path, crypto/frost/src/sign.rs:127-141) and identical `msg`.
3. Both sessions compute `rho_transcript` over `(group_key, hash_msg(msg), hash(preprocesses))` → identical `rho_i`, identical `R = Σ D_i + rho_i·E_i`.
4. Session A shares: `sA_i = n_i + c_A·λ_i·x_i` with `c_A = Hram(R, Y, ctxA||msg)`; session B: `sB_i = n_i + c_B·λ_i·x_i` with `c_B = Hram(R, Y, ctxB||msg)`.
5. `x_i = (sA_i − sB_i) / ((c_A − c_B)·λ_i)` — attacker recovers each of the `t` secret shares and the group private key from data that is broadcast in the clear (`SignatureShare`, crypto/frost/src/sign.rs:183).

### Citations

**File:** crypto/frost/src/sign.rs (L362-368)
```rust
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );
```
