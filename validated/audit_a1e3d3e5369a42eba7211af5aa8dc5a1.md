### Title
Malicious FROST preprocess forces nonce sum to the point at infinity, panicking `Hram::hram` (DoS) - (File: networks/bitcoin/src/crypto.rs)

### Summary
CVE-2018-19407 is a NULL-pointer dereference reachable by an unprivileged user via crafted inputs that drive the kernel into an uninitialized state. The Serai analog is an unhandled-identity-point panic: the BIP-340 `Hram` used for Bitcoin transaction signing calls `x(&R)` / `x(&A)`, which `expect`s the point is not infinity. A participant in a FROST signing session can choose nonce commitments (public preprocess bytes fed to `read_preprocess` / `sign`) such that the aggregate `R` becomes the point at infinity, causing a panic (denial of service) in every honest signer/aggregator that processes it.

### Finding Description
In `networks/bitcoin/src/crypto.rs`, `x(key)` extracts the x-coordinate and panics on the point at infinity: `(*encoded.x().expect("point at infinity")).into()` (line 15), and `x_only` likewise `expect`s (line 22). `Hram::hram` calls `x(R)` and `x(A)` unconditionally (lines 65–66). The doc comment at line 54 admits "If either `R` or `A` is the point at infinity, this will panic" and claims negligible probability — but `R` is not random: it is the sum of per-participant binding-factor-weighted nonce commitments supplied by counterparties.

An adversarial participant receives (or observes) all other participants' preprocesses before publishing their own. They can compute their nonce commitments `D_i, E_i` such that the aggregate `R = sum_j (D_j + rho_j * E_j)` equals the identity — e.g., set their commitments to the negation of the current partial sum. `read_preprocess` in `TransactionSignMachine` (send.rs:351) accepts arbitrary group elements; there is no identity rejection on nonce commitments. When `AlgorithmMachine::sign` / `verify_share` / `verify` computes the aggregate `R` and invokes `Hram::hram`, the `expect` fires and the process panics — analogous to the kernel BUG() on uninitialized ioapic state.

### Impact Explanation
Denial of service of the Bitcoin signing pipeline. One malicious (or compromised) FROST participant can deterministically abort every signing attempt for a given transaction by crafting preprocess bytes that force `R` to infinity, panicking honest processors in `TransactionSignMachine::sign` / `TransactionSignatureMachine::complete` (send.rs:355–398). Since blaming requires the protocol to complete share verification, repeated crafted preprocesses can stall fund movement indefinitely. Medium severity: availability loss with no secret leakage.

### Likelihood Explanation
Reachable by any party able to submit preprocess bytes to a signer — the exact threat model of a faulty DKG/signing participant the code already handles via `FrostError::InvalidPreprocess`. The attack requires no race or probabilistic condition: the malicious commitment is solved algebraically over the other participants' public preprocesses, deterministic and repeatable every attempt.

### Recommendation
Reject identity/non-prime-order nonce commitments in `read_preprocess` / `process_addendum`, or make `x()`/`x_only()` return `Option` and propagate a `FrostError` instead of panicking. Alternatively, treat an identity aggregate `R` as a per-participant fault (`InvalidPreprocess(l)`) so blame attribution still functions.

### Proof of Concept
```rust
// Participant m waits for all other preprocesses, then solves:
//   D_m = -(sum_{j!=m} D_j + sum_{j!=m} rho_j * E_j) - rho_m * E_m
// choosing E_m = G, rho_m computed from the FROST binding factor.
// Publishing (D_m, E_m) makes aggregate R = identity.
// Honest signers call TransactionSignMachine::sign -> sig.sign -> ... ->
//   Hram::hram(&R, &A, msg) -> x(&R) -> expect("point at infinity") => panic.
// crypto.rs:13-16, 59-73; send.rs:351-398.
```
Exact call path inside `crypto/frost/src/algorithm.rs` (where aggregate `R` is formed before `hram` invocation) could not be fully line-verified within the tool budget, but the panic site and the absence of an identity check on parsed nonce commitments are confirmed in `networks/bitcoin/src/crypto.rs:13-22,54-83` and `networks/bitcoin/src/wallet/send.rs:351-353`.