### Title
Attacker-forced identity nonce sum panics in BIP-340 HRAM during FROST signing - ([File: networks/bitcoin/src/crypto.rs])

### Summary
The bug class in CVE-2021-34340 is an out-of-bounds access on attacker-influenced data producing a hard crash (denial of service). In Serai's Rust code the equivalent is an unchecked panic reachable from untrusted protocol bytes. The Bitcoin Schnorr `Algorithm` implementation calls `Hram::hram`, which unconditionally calls `x()` on the aggregated nonce point `R`. `x()` panics on the point at infinity (`crypto.rs:13-16`, `crypto.rs:21-23`). A malicious co-signer can craft their `Preprocess` nonce commitments so the aggregate nonce sum `R` is the identity, causing every honest participant's `AlgorithmSignMachine::sign` (which reaches `Schnorr::sign_share` → `Hram::hram(&nonce_sums[0][0], ...)`) to panic, aborting the signing session and crashing the calling code path.

### Finding Description
- `Hram::hram` for `Secp256k1` extracts the x-coordinate of `R` and `A` via `x()`, documented as "Panics on invalid input" and implemented with `.expect("point at infinity")` on `encoded.x()` (`networks/bitcoin/src/crypto.rs:13-23`, `crypto.rs:54-73`).
- The FROST preprocess parser `Commitments::read` (invoked through `AlgorithmSignMachine::read_preprocess`, `crypto/frost/src/sign.rs:276-281`) reads each participant's nonce commitments via `C::read_G`. Identity points are canonical encodings and are not rejected.
- During `sign`, the per-participant binding factors `rho_l` are computed deterministically from a transcript over all preprocesses (`crypto/frost/src/sign.rs:361-371`), then nonce sums are formed as `R = Σ (D_l + rho_l · E_l)`. A participant who supplies their preprocess last (or who can predict/coordinate preprocess ordering) can choose `D_mal = -(Σ_{l≠mal}(D_l + rho_l·E_l)) - rho_mal·E_mal` with arbitrary non-identity `E_mal`, forcing the aggregate `nonce_sums[0][0]` to identity even though every individual commitment is a valid, non-identity, canonically encoded point.
- `sign_share` then calls `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` (`crypto/frost/src/algorithm.rs:208`), which panics inside `x()`. `TransactionSignMachine::sign` in `networks/bitcoin/src/wallet/send.rs:355-398` propagates this through `sig.sign(...)` for each input, so a crafted preprocess crashes Bitcoin transaction signing.

### Impact Explanation
An untrusted participant in a signing set can deterministically panic honest signers by submitting a maliciously computed preprocess through `read_preprocess`/`sign`. This aborts threshold signing of Bitcoin transactions (denial of service) at the exact analog of the Ming OOB read: attacker-controlled input drives a crash in the parsing/processing path. Severity is bounded to DoS; no key material is leaked.

### Likelihood Explanation
Reachability requires the attacker to be a co-signer in the threshold (supplying preprocess bytes) and to compute `rho` for the session, which is deterministic given the preprocess set. Since preprocess exchange happens before share generation, a signer's malicious commitment directly triggers the panic on all honest participants' machines when they call `sign`. No collusion beyond the single malicious participant is needed; the attacker's preprocess is fully under their control.

### Recommendation
Reject identity nonce commitments in `Commitments::read` (check `is_identity` on each `C::read_G` result), and/or check `nonce_sums` for identity before invoking the HRAM in `sign_share`/`verify`, returning `FrostError` instead of panicking. More broadly, `x()`/`x_only()` in `networks/bitcoin/src/crypto.rs` should return `Option`/`io::Result` rather than panicking on the point at infinity so any residual path fails gracefully.

### Proof of Concept
```rust
// Within a FROST signing session for Secp256k1 (networks/bitcoin Schnorr
// algorithm): attacker participant l* waits for all other preprocesses,
// computes rho_l* from the rho transcript over B, chooses arbitrary
// E_star = k*G, and sets:
//   D_star = -(sum over l != l* of (D_l + rho_l * E_l)) - rho_lstar * E_star
// The preprocess serializes as canonical points (D_star, E_star), which pass
// `read_preprocess`/`Commitments::read` since both are valid non-identity
// points. When each honest participant calls `sign(preprocesses, msg)`:
//   nonce_sums[0][0] = sum(D_l + rho_l * E_l) == identity
//   Schnorr::sign_share -> Hram::hram(&identity, &group_key, msg)
//     -> x(&identity) -> to_encoded_point(true).x() == None
//     -> .expect("point at infinity") -> panic!()
// Result: signing session aborts via panic on all honest participants.
```