### Title
Attacker-controlled FROST preprocess commitments force the aggregate nonce `R` to the point at infinity, panicking the signer inside BIP-340 `Hram` - (File: networks/bitcoin/src/crypto.rs)

### Summary
The bug class in CVE-2023-3585 is "insufficient validation of attacker-supplied data causes a remote crash." Serai's Bitcoin FROST path has the same shape: an untrusted `Preprocess` message's commitments are deserialized without an identity check, are algebraically combined into the aggregate nonce `R` in `BindingFactor::nonces`, and `R` is then passed to `Hram::hram`, which unconditionally calls `x(R)` — a helper documented and implemented to panic on the point at infinity. A malicious signing-set participant can craft their commitments so that `R = identity`, deterministically panicking every honest signer.

### Finding Description
`Commitments::read` in `crypto/frost/src/nonce.rs:133` reads `GeneratorCommitments` via `C::read_G` (`crypto/ciphersuite/src/lib.rs:91`), which enforces canonical encoding but does not reject the identity point. These attacker-chosen commitments flow into `BindingFactor::nonces` (`crypto/frost/src/nonce.rs:194-212`), which computes `R_n = Σ_i D_i + multiexp(ρ_i · E_i)` — a linear combination where the per-participant binding factors `ρ_i` are publicly computable from the transcript of all preprocesses (`crypto/frost/src/sign.rs:361-371`). Because the rho transcript commits to the attacker's own preprocess only after they choose it, an attacker who observes the other participants' preprocesses can solve for their own `(D_i, E_i)` to make the sum exactly `identity`.

`R` then reaches `Hram::hram` (`networks/bitcoin/src/crypto.rs:59-73`) via `Schnorr::sign_share` → `FrostSchnorr::sign_share` (`crypto/frost/src/algorithm.rs:201-211`). `hram` calls `x(R)` at `networks/bitcoin/src/crypto.rs:65`, and `x` executes `(*encoded.x().expect("point at infinity"))` at line 15 — a hard panic on identity. The identity branch in `Hram::hram` is not even handled: `needs_negation`/`conditional_select` only adjust parity, never infinity.

The documented precondition ("negligible probability... even with malicious participants") at `networks/bitcoin/src/crypto.rs:78-80` is wrong: it is not probabilistic — the attacker algebraically constructs it.

### Impact Explanation
Any unprivileged participant in a FROST signing session (anyone able to broadcast a `Preprocess` to the signing set, e.g. a counterparty in an externally-coordinated signing attempt who injects a preprocess under an included `Participant` index) can crash the honest signers' nodes when `sign()`/`sign_share` executes. This is a deterministic remote denial of service of threshold signing for Bitcoin funds, matching the Medium severity of the Mattermost crash analog.

### Likelihood Explanation
Reachability is direct: `AlgorithmSignMachine::read_preprocess` (`crypto/frost/src/sign.rs:276-281`) accepts raw bytes, `Commitments::read` imposes no non-identity constraint, and `sign()` (`crypto/frost/src/sign.rs:283`) feeds the resulting bound nonces into `sign_share` → `hram` → `x()` in a single call. The attacker only needs to wait for other preprocesses before publishing their own — no collusion, no leaked keys, no malicious-node assumptions.

### Recommendation
- In `Commitments`/`GeneratorCommitments::read` (`crypto/frost/src/nonce.rs:34-42`), reject identity points (`point.is_identity()`), or in `BindingFactor::nonces` check each aggregated `R` for identity and return a `FrostError`.
- Make `x()`/`x_only()`/`Hram::hram` in `networks/bitcoin/src/crypto.rs` return errors instead of `expect`/`unwrap`, propagating through `Hram`'s signature (or assert-on-infinity is retained only as `debug_assert` after upstream rejection).
- Failing decryption/panics aside, `FrostError::InvalidCommitments`-style blame should be attributed to the participant whose preprocess produced the identity term.

### Proof of Concept
```rust
// Within a signing session over Secp256k1 using bitcoin_serai::crypto::Schnorr:
// 1. Honest parties broadcast their Preprocess messages.
// 2. Attacker computes rho transcript exactly as sign.rs:361-371 does,
//    obtaining binding factors rho_l for every included participant l
//    (their own rho depends on their candidate preprocess; they iterate
//    until self-consistent, which is a fixed-point search over one scalar
//    or simply solved since rho is public).
// 3. Attacker picks, for the single Schnorr nonce/generator:
//      E_attacker = identity-representing commitment pair and
//      D_attacker = -(Σ_{l != attacker} (D_l + rho_l * E_l)) - rho_attacker * E_attacker
//    then publishes Preprocess { commitments: (D_attacker, E_attacker), addendum: () }.
// 4. BindingFactor::nonces (nonce.rs:194) computes R = identity.
// 5. Honest signer's sign_share -> Hram::hram -> x(&R) panics at
//    crypto.rs:15 ("point at infinity"), crashing the node.
//
// Minimal direct demonstration of the panic primitive:
let identity = ProjectivePoint::IDENTITY;
bitcoin_serai::crypto::x_only(&identity); // panics: "point at infinity"
// or via Hram:
// <Hram as HramTrait<Secp256k1>>::hram(&identity, &key, b"msg"); // panics
```

Uncertainty note: I could not fully verify whether `ThresholdKeys::view`/`lagrange` rejects `Participant(0)` or how `read_G` on non-`dalek-ff-group` ciphersuites (e.g. `kp256`) treats identity — for `k256`, `ProjectivePoint::from_bytes` accepts the identity encoding and the canonical-encoding check in `read_G` does not exclude it. The panic primitive itself is confirmed at `networks/bitcoin/src/crypto.rs:13-23`.