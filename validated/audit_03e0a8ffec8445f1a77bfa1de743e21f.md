### Title
DkgConfirmer `complete` re-signs with cached nonces, enabling nonce reuse and validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug class (CVE-2021-47394) is *use-after-delete*: an object that was logically removed remains reachable to lockless readers. The Serai analog is *use-after-consume*: `SigningProtocol::preprocess_internal` loads a deterministic ChaCha20 seed (`CachedPreprocess`) from `CachedPreprocesses` keyed only by `(b"DkgConfirmer", attempt)`, and **never deletes or rotates it** — there is no `CachedPreprocesses::del`/`remove` anywhere in the codebase. Every call to `sign` under the same attempt reuses identical nonce commitments. `DkgConfirmer::complete` unconditionally invokes `share_internal` a *second* time (`signing_protocol.rs:321-324`), so if a validator already published its share via `share()` (or `preprocess()`/`share()` are re-driven by re-execution after a rebuild), `complete` produces a second signature share under the same nonces. If the preprocess set or `key_pair` (which feeds `set_keys_message`, the signed msg) differs between the two calls, the binding factor/challenge differs while the nonce is identical — the classic FROST/Schnorr nonce-reuse linear system that recovers the signer's secret share.

### Finding Description
- `preprocess_internal` checks `CachedPreprocesses::get(...)`, seeds a machine from OsRng only if absent, XORs with an encryption key, stores it, then *re-reads and reuses* it every invocation (`signing_protocol.rs:123-145`). The entry is never removed after signing.
- `share_internal` calls `self.preprocess_internal(participants).0` then `machine.sign(preprocesses, msg)` (`signing_protocol.rs:156-170`).
- `complete` calls `self.share_internal(preprocesses, key_pair)` again purely to rebuild the machine (`signing_protocol.rs:321-324`), emitting a second `SignatureShare` under the same nonces for whatever `(preprocesses, key_pair)` it is invoked with.
- The file's own safety argument (lines 34-35) states: "In order for nonce re-use to occur, the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" — precisely what `complete` does when driven with inputs distinct from the earlier `share` call (e.g., a different finalized preprocess map or different `KeyPair` in the confirmation being completed).

### Impact Explanation
Two FROST signature shares sharing the same nonce pair but different aggregate binding factors/challenges yield linear equations in the signer's secret nonce scalars; recovering the nonces plus the two public shares exposes the validator's MuSig secret-share contribution, i.e. private key share recovery — a Critical-impact outcome per the code's own comments ("Reusing preprocesses would enable a third-party to recover your private key share", `sign.rs:85-86`).

### Likelihood Explanation
Reachable by an unprivileged network participant only indirectly: the inputs to `share`/`complete` are BFT-finalized preprocess/key-pair data, and the safety comment argues distinct finalized messages cannot occur absent re-orgs or rebuilds. However, the code performs no defensive check (the TODO at line 51 admits the "commitments match presumed" check is unimplemented), never deletes the used cache, and `complete` re-signs unconditionally. Any path that re-drives `share_internal` with different finalized preprocesses — e.g., a coordinator handling a confirmation for a `key_pair`/preprocess set differing from the one it already published a share for — triggers deterministic nonce reuse. This is a structural hazard of the same shape as the CVE (an object consumed-but-not-unlinked), not a hypothetical memory bug.

### Recommendation
After `share_internal` signs, delete or tombstone `CachedPreprocesses` for the context (`CachedPreprocesses::del`), and make `complete` refuse to re-`sign` (rebuild only the deterministic machine state needed for `complete`, or persist and reuse the already-produced share). Implement the noted TODO: verify the preprocess bytes previously published on-chain match the ones derived from the cached seed before publishing shares again.

### Proof of Concept
1. For attempt `a`, `preprocess()` → `share(preprocesses_A, kp_A)` publishes share `s_A` computed from nonces `d, e` derived from the cached seed.
2. `complete(preprocesses_B, kp_B, shares)` is invoked where the finalized preprocess map or `KeyPair` differs (different participants/bytes or different key pair).
3. `share_internal` reloads the identical seed (`signing_protocol.rs:137`), regenerates `d, e`, and `sign` produces `s_B` with the same nonces but a different binding factor ρ/challenge c.
4. From `s_A`, `s_B`, the public commitment `(D, E)`, ρ_A, ρ_B, c_A, c_B: solve `d + e·ρ` from the two share equations (`s_i = d_i + e_i·ρ_i·λ + c_i·x`), then recover secret share `x`. Two attempts suffice since `s_i` differ only in the challenge term once nonces cancel.

Caveat: full reachability depends on a finalized-message divergence, which the design assumes BFT prevents; the unimplemented check at line 51 and the un-deleted cache are the concrete defects making the invariant load-bearing rather than enforced.