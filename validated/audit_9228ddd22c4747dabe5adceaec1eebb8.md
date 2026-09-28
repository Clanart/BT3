### Title
Attacker-controlled preprocess forcing the aggregate nonce `R` to the point at infinity crashes `Hram::hram`/`x()` in BIP-340 signing - ([File: networks/bitcoin/src/crypto.rs])

### Summary
CVE-2019-11365's class is *a crafted packet triggers memory corruption / a crash in the parser-handler of untrusted input*. In Serai's Rust code the reachable analog is a deterministic panic on attacker-controlled input: the Bitcoin `Hram::hram` unconditionally calls `x(R)`, which `expect`s the point is not the identity. A malicious signing participant can craft nonce commitments so the aggregated `R` equals the point at infinity, panicking every honest signer inside `sign_share`/`verify` — an unprivileged-party crash reachable purely through `read_preprocess`-fed bytes.

### Finding Description
`x()` panics when passed the point at infinity:

`networks/bitcoin/src/crypto.rs:13-16`
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

`Hram::hram` calls `x(R)` where `R` is the aggregated group nonce (`crypto.rs:59-67`). In FROST signing, `R` is formed as the sum of each participant's bound nonce commitments: `BindingFactor::nonces` computes `D = Σ commitments.nonces[n].generators[g].0[0]` plus `rho`-weighted `0[1]` terms (`crypto/frost/src/nonce.rs:194-212`). The per-participant commitments come from untrusted bytes via `Commitments::read` → `read_preprocess` (`crypto/frost/src/sign.rs:276-281`). There is no identity check on the aggregate `R` before `hram` is invoked.

A malicious participant who sends their preprocess last (or in a second/parallel attempt after seeing honest commitments) can set `GeneratorCommitments([D, E])` such that `D + rho*E` exactly cancels the sum of all other participants' bound nonces, making the aggregate `R = identity`. When `Algorithm::sign_share`/`verify` for `Schnorr` (bitcoin's wrapper around `IetfSchnorr`) evaluates `Hram::hram(&R, &group_key, msg)`, `x(R)` hits `.expect("point at infinity")` and panics, killing the signing session on every honest node that processes the preprocess set.

The doc comment acknowledges only a "negligible probability" for honest callers (`crypto.rs:78-83`), but the identity nonce is deterministically constructible by a malicious *participant* — it is not negligible under adversarial preprocesses. Note `needs_negation`/the identity case produces no valid signature either way, so the same panic occurs in `complete`/`verify`.

### Impact Explanation
An unprivileged counterparty in any FROST/BIP-340 signing session can deterministically crash honest signers by supplying a crafted (but well-formed, canonical-encoding) preprocess. This aborts the signing attempt for all inputs (a `TransactionSignMachine` signs all inputs; the panic occurs while iterating inputs). Severity: Medium — availability loss (panic) reachable from public preprocess bytes, with no key leakage.

### Likelihood Explanation
Requires the attacker to be a signing-set participant who can choose commitments after (or with knowledge of) the honest aggregate — feasible for a rushing adversary or in retry/parallel-session flows where preprocesses are re-collected. The malformed preprocess parses cleanly because `Commitments::read` only enforces canonical point encodings, not non-cancellation of the aggregate.

### Recommendation
Reject identity aggregate nonces before calling `hram`: in `Schnorr::sign_share`/`verify` (or in FROST's nonce aggregation) check `R.is_identity()` and return `FrostError::InvalidPreprocess(participant)`/`None` instead of letting `x()` panic. Alternatively, make `x()`/`x_only()` return `Option`/`Result` and propagate an error rather than `expect`.

### Proof of Concept
1. Honest participants broadcast `GeneratorCommitments` for nonce `n`; attacker observes them (rushing).
2. Attacker computes `rho` for themselves from the transcript, then chooses `D_attacker = -Σ_{l≠attacker}(D_l + rho_l*E_l) - rho_attacker*E_attacker` for an arbitrary `E_attacker`, and submits `(D_attacker, E_attacker)` as their preprocess — both points are valid canonical encodings, so `read_preprocess` succeeds.
3. Aggregate `R = identity`; `sig.sign(...)` → `sign_share` → `Hram::hram(R, A, msg)` → `x(R)` → panic at `crypto.rs:15`.

Uncertainty noted: whether Serai's higher-level coordinator (tributary ordering) prevents a participant from choosing commitments after seeing others' — the panic itself is still reachable on any `Retry`/`attempt` flow or unordered preprocess gossip where ordering isn't strictly enforced.