### Title
Attacker-controlled FROST preprocess forces identity nonce sum, panicking `x()` in BIP-340 `Hram` during `sign_share` — denial of service - ([File: networks/bitcoin/src/crypto.rs])

### Summary
An unprivileged signing participant can craft a preprocess whose nonce commitments drive the aggregate nonce `R` to the point at infinity. The Bitcoin `Hram` calls `x(R)`, which `expect`s a non-infinity point and panics. Every honest signer crashes inside `machine.sign(...)` before any `FrostError` can be returned, halting threshold signing.

### Finding Description
`Commitments::read` (via `C::read_G`, crypto/ciphersuite/src/lib.rs:91-101) accepts the identity point since it is a canonical encoding, and `AlgorithmSignMachine::sign` (crypto/frost/src/sign.rs:283+) validates only participant indexes, not nonce values. `Schnorr::sign_share` (crypto/frost/src/algorithm.rs:208) calls `H::hram(&nonce_sums[0][0], ...)` where `nonce_sums[0][0]` is the sum `Σ_j D_j + ρ_j·E_j` over all participants' preprocessed commitments. For the Bitcoin algorithm, `Hram::hram` (crypto.rs:59-73) calls `x(R)` → `encoded.x().expect("point at infinity")` (crypto.rs:13-16), and `needs_negation`/`x_only` share the same assumption.

A malicious participant who transmits their preprocess last knows all honest commitments and can compute their own binding factor `ρ_i`, then choose `E_i = identity` and `D_i = -(Σ_{j≠i} D_j + ρ_j·E_j)`, forcing `nonce_sums[0][0]` to identity. `read_preprocess` accepts this encoding, and `sign` panics rather than returning `FrostError::InvalidPreprocess`.

### Impact Explanation
Any participant in a Bitcoin FROST signing session can crash every honest signer's processor/coordinator task at `machine.sign`, denying service for the signing attempt and — since the panic propagates through `processor/src/signer.rs`, `batch_signer.rs`, `cosigner.rs`, etc. — potentially crashing the node. This is a remotely triggerable denial of service via untrusted bytes fed to `read_preprocess`, mirroring the CVE-2018-18023 class (crafted input → crash/DoS).

### Likelihood Explanation
Reachable by any unprivileged party able to join the signing set and supply preprocess bytes; no key material or collusion required. The attacker only needs to order their preprocess after learning others' (achievable by responding last). Probability of the panic is deterministic once the crafted `D_i`/`E_i` are supplied.

### Recommendation
Reject identity (and optionally non-even-Y) nonce commitments in `Commitments::read` / `validate_map`, or have `Hram`/the sign path return `FrostError` instead of panicking when `R` is the point at infinity — e.g., check `R.is_identity()` in `sign_share`/`verify` before calling `x()`.

### Proof of Concept
Conceptual: in a Secp256k1 `bitcoin_serai::Schnorr` FROST session, after collecting all honest `Preprocess` messages, the attacker computes the honest aggregate `S = Σ_{j≠i} (D_j + ρ_j·E_j)`, then serializes a preprocess with `E_i = identity` and `D_i = -S`, submits it via `read_preprocess`, and every honest signer's `machine.sign(preprocesses, msg)` panics at `crypto.rs:15` (`expect("point at infinity")`) inside `Hram::hram`.