### Title
Malicious FROST preprocess forces point-at-infinity R, crashing signers via `x()`/`hram` panic - (File: networks/bitcoin/src/crypto.rs)

### Summary
The BIP-340 `Hram` implementation panics when the aggregate nonce `R` or group key `A` is the point at infinity (`x()` calls `expect("point at infinity")` on `encoded.x()`). A malicious signing participant can craft nonce commitments in their preprocess so the aggregate nonce sum is the identity, turning `sign_share` / `verify` into an unhandled panic (denial of service) inside `TransactionSignMachine::sign`/`TransactionSignatureMachine::complete` for every honest signer — the analog of fig2dev's crash-on-malformed-input.

### Finding Description
`x()` at `networks/bitcoin/src/crypto.rs:13-16` does `key.to_encoded_point(true).x().expect("point at infinity")`. `Hram::hram` (`crypto.rs:59-73`) calls `x(R)` and `x(A)`, and `Schnorr::verify` additionally calls `needs_negation(&sig.R)` (`crypto.rs:146`). The doc comment (`crypto.rs:78-83`) acknowledges: "This may panic if called with nonces/a group key which are the point at infinity (which have a negligible probability for a well-reasoned caller, **even with malicious participants present**)."

That assumption is wrong for `R`. In FROST, each participant contributes nonce commitments through `Preprocess` messages parsed by `read_preprocess` (`crypto/frost/src/sign.rs:276-281`), and the per-signer `R` is the binding-factor-weighted sum of everyone's commitments (`BindingFactor`/`B.nonces`, `crypto/frost/src/sign.rs:382-398`). An attacker who submits their preprocess after observing the other participants' preprocesses (submit-last / ROS-style adaptivity) can choose a commitment vector that makes the aggregate `R` equal to the identity — e.g., by supplying the negation of the current commitment sum, since nothing in `read_preprocess`/`Commitments::read` binds a participant's preprocess to values committed before seeing others. When `Schnorr::sign_share` or `verify` then computes `hram`, `x(R)` panics.

### Impact Explanation
The panic propagates out of `sign`/`complete` rather than returning `FrostError`, crashing the signing task/thread for every honest participant that processes the malicious preprocess — and in the bitcoin path (`TransactionSignMachine::sign`, `send.rs:355-398`) it aborts the whole multi-input signing loop. For an async caller this can take down the processor's signing task; in `panic = "abort"` builds it kills the node. This is a remote, untrusted-input-triggered denial of service, matching the CVE class (crash from attacker-controlled message bytes). The same panic also fires in `verify` during `complete`, so even the blame/identification path (`crypto/frost/src/sign.rs:465-489`) dies before the malicious participant can be attributed via `FrostError::InvalidShare`.

### Likelihood Explanation
Reachability requires only that `read_G` accept the encodings needed for a cancelling commitment — `read_preprocess` imposes no uniqueness or "submitted before seeing others" constraint, so an attacker in the signing set (or who can feed a participant a forged preprocess blob, since preprocesses are exchanged as raw bytes per `machine.read_preprocess(&mut share_ref)` in `processor/src/signer.rs:587-588`) can satisfy it by submitting last, a standard practical assumption for nonce-commitment rounds. One caveat I could not fully verify: whether `Secp256k1::read_G` (kp256) rejects the identity encoding itself — but the attack does not need an identity *encoding*, only identity as a *sum* of valid non-identity points, which is accepted. Severity: Medium (availability only, no secret leakage or forged output).

### Recommendation
- Reject/pre-handle `R` and `A` being identity: check `R.is_identity()` / `A.is_identity()` in `Hram::hram` (and `sig.R` in `Schnorr::verify`) and return `None`/an error rather than panicking through `x()`.
- More robustly, validate in `AlgorithmSignMachine::sign` that the computed aggregate nonces `Rs` are non-identity before calling `sign_share`, returning `FrostError::InvalidPreprocess(l)` where the malicious preprocess can be identified, so blame attribution still works.
- Document that preprocess ordering must prevent adaptive (submit-last) commitment selection, or add a commitment round.

### Proof of Concept
Conceptual (library-level):
1. Honest signers publish preprocesses giving nonce sums `S = Σ ρ_i · (commitments_i)` for the first generator set.
2. Attacker computes their `GeneratorCommitments` as `[-S_forced]` chosen so that after binding factors, `B.nonces(...)` yields `R = identity` for the aggregated nonce vector (submitting last; for the multi-nonce case solve per-index).
3. Every honest signer calls `machine.read_preprocess(attacker_bytes)` (succeeds — points are valid), then `sign(...)`.
4. Inside `sign_share` → `Hram::hram(&R=identity, &group_key, msg)` → `x(&identity)` → `to_encoded_point(true).x()` returns `None` → `expect("point at infinity")` panics (`crypto.rs:15`), aborting the signing task. `complete`/`verify` panic identically via `needs_negation`/`hram`, so `FrostError::InvalidShare` blame is never reached.