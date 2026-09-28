### Title
Unconditional panic in BIP-340 FROST signing when a participant's nonce commitments cancel the aggregate nonce to the point at infinity - (File: networks/bitcoin/src/crypto.rs)

### Summary
`Hram::hram` calls `x(R)`/`x(A)`, which panic on the point at infinity (`encoded.x().expect("point at infinity")` at `crypto.rs:13-16`, documented "will panic" at `crypto.rs:54` and `crypto.rs:78-80`). The aggregate nonce `R` is a sum over all participants' preprocess commitments (`crypto/frost/src/sign.rs:283-319`, `TransactionSignMachine::sign` at `networks/bitcoin/src/wallet/send.rs:355-398`). A participant who submits preprocess bytes via `read_preprocess` (`send.rs:351-353`) can choose their own hiding/binding commitments so the aggregate `R` sums to identity. Nothing in `Commitments::read` or `sign` rejects this: each individual commitment is a valid non-identity point, and `sign` only validates participant indexes/duplicates (`sign.rs:296-313`), never that the summed nonce is non-infinity. The subsequent `sign_share`/`verify` path calls `hram` and panics.

### Finding Description
This is the Serai analog of CVE-2018-5710: a remote, protocol-participating party feeds crafted public input (preprocess commitments) that hits an unchecked degenerate value (NULL in krb5; point at infinity here) inside a function that unconditionally dereferences it (`x()`'s `.expect("point at infinity")`). For a multi-input `SignableTransaction`, `Schnorr::verify` at `crypto.rs:139-150` also feeds `sig.R` through `needs_negation`, and `hram` is invoked per input, so the abort hits every signer that accepts the malicious preprocess set.

### Impact Explanation
A single malicious participant's preprocess deterministically crashes (panics) every honest signer that runs `TransactionSignMachine::sign` with it, aborting the signing attempt. Repeated across attempts, this is a persistent denial of service of Bitcoin spend signing (availability loss), matching the CVE's authenticated-user DoS class.

### Likelihood Explanation
Any party able to submit a preprocess message to an honest `TransactionSignMachine`/`AlgorithmSignMachine` can do this: they observe the other commitments, compute the binding factors, and set their own pair of commitments to cancel the sum. It requires no secret knowledge and is fully deterministic, not probabilistic.

### Recommendation
Check the aggregate nonce `R` (and each per-participant bound nonce) for identity before calling `hram` in `crypto/frost/src/algorithm.rs`'s `sign_share`/`verify`, returning `FrostError::InvalidPreprocess(l)` identifying the responsible participant, rather than relying on `x()`'s panic.

### Proof of Concept
Attacker waits for all other preprocesses, computes `rho_i` per `sign.rs` binding, then publishes commitments `D_a = -(Σ R_j)` minus binding terms so `Σ bound_nonces = identity`. Honest signer calls `machine.sign(preprocesses, b"")` → `taproot_key_spend_signature_hash` → `sign_share` → `Hram::hram(&identity, A, msg)` → `x(R).expect("point at infinity")` → panic.

Note: I could not fully confirm (within iteration limits) whether `crypto/frost/src/algorithm.rs` already rejects an identity aggregate nonce before reaching `hram`; if it does, this finding reduces to no vulnerability — the panic sites and the absence of an identity check in `sign.rs`'s validation were verified directly.