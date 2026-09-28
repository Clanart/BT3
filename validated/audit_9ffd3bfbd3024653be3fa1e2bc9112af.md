### Title
Attacker-controlled preprocess forces aggregate nonce `R` to the point at infinity, panicking `Hram::hram`/`x` during signing — reachable local DoS - (File: networks/bitcoin/src/crypto.rs)

### Summary
CVE-2022-4127 is a null-pointer dereference reachable by a local user causing a denial of service. The Serai analog is a panic-on-identity reachable by an unprivileged signing-set participant through public preprocess bytes: `x()` panics on the point at infinity, and `bitcoin_serai::crypto::Hram::hram` calls `x(R)` on the aggregate FROST nonce commitment. An attacker who supplies a `Commitments` preprocess (read via `SignMachine::read_preprocess` / `Commitments::read`) can choose their commitment points so that the aggregated nonce sum `nonce_sums[0][0]` is exactly the identity, causing a panic in `sign_share` (and again in `verify`/`verify_share` during `complete`).

### Finding Description
- `x()` panics on infinity: `(*encoded.x().expect("point at infinity"))` — `networks/bitcoin/src/crypto.rs:13-16`.
- `Hram::hram` calls `x(R)` and `x(A)` unconditionally — `crypto.rs:59-67`. The doc comment admits: "If either `R` or `A` is the point at infinity, this will panic" (`crypto.rs:54`).
- The aggregate nonce is computed in `BindingFactor::nonces` as `D = Σ_i (D_i + rho_i * E_i)` over all participants' preprocess commitments — `crypto/frost/src/nonce.rs:194-212`.
- Each individual commitment point is required to be non-identity (`Curve::read_G` rejects identity, `crypto/frost/src/curve/mod.rs:125-131`), but there is no check that the *sum* is non-identity.
- Binding factors `rho_i` are public, deterministic hashes (`C::hash_binding_factor` over the `rho_transcript` of all preprocesses, `crypto/frost/src/sign.rs:361-371`). A participant who chooses their preprocess last can compute every `rho_i` locally, then set their own `D_i` such that `D_i = -(Σ_{j≠i}(D_j + rho_j·E_j)) - rho_i·E_i`, forcing `R = Σ(D_j + rho_j·E_j)` to identity.
- During `sign`, `sign_share` calls `H::hram(&nonce_sums[0][0], …)` (`crypto/frost/src/algorithm.rs:208` → `crypto.rs:65`), panicking. During `complete`, `verify`/`verify_share` call `hram`/`batch_statements` on the same `R` (`algorithm.rs:215-227`), so the completing node panics too. Non-identity `D_i`/`E_i` individually satisfies `read_G`, so the malicious preprocess parses cleanly.

This is computable deterministically — not the "negligible probability" assumed in the `crypto.rs:78-80` comment — because `rho_i` is a pure function of the public preprocess bytes.

### Impact Explanation
Any participant in a FROST signing set for the Bitcoin (Taproot/BIP-340) algorithm can deterministically crash every honest signer's process by submitting a crafted `Preprocess`/`Commitments` message. This is a denial of service of the threshold-signing pipeline: the panicking `sign`/`complete` aborts signing of Bitcoin transactions. Reachability is via untrusted bytes to `read_preprocess` → `sign`/`complete`, matching the rules.

### Likelihood Explanation
Requires the attacker to be a member of the signing set (or otherwise have preprocess bytes accepted for a signing session) and to order/choose their preprocess after learning peers' commitments — standard in preprocess-exchange protocols. No secret material needed; rho is publicly computable. Medium severity mirrors the source CVE (local crash DoS, no secret leakage).

### Recommendation
After `BindingFactor::nonces` computes each `nonce_sums[n][g]`, reject the signing set (return `FrostError::InvalidPreprocess`/`InvalidSigningSet`) if any aggregated commitment is the identity, rather than deferring to a panic inside the HRAm. Alternatively, make `x()`/`Hram::hram` return `Option`/`Result` and propagate an error. Checking `R.is_identity()` in `AlgorithmSignMachine::sign` before `sign_share`, and in `AlgorithmSignatureMachine::complete` before `verify`/`verify_share`, covers both crash sites.

### Proof of Concept
1. Honest parties publish preprocesses `(D_j, E_j)`.
2. Attacker fixes `E_i` (e.g., `G`), reconstructs `rho_transcript` (`sign.rs:362-368`), computes `rho_i = C::hash_binding_factor(transcript || participant_i)` and all `rho_j`.
3. Attacker sets `D_i = -(Σ_{j≠i} D_j + Σ_{j≠i} rho_j·E_j) - rho_i·E_i`, encodes `(D_i, E_i)` — both non-identity, so `Commitments::read`/`read_G` accepts.
4. `BindingFactor::nonces` yields `R = identity`; `Schnorr::sign_share` → `Hram::hram` → `x(R)` → `expect("point at infinity")` panics; the same panic hits `complete` via `verify`/`verify_share`.