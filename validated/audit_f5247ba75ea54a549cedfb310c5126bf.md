### Title
Attacker-controlled FROST preprocess can force the aggregate nonce `R` to the point at infinity, panicking `x()`/`hram` and crashing every co-signer - ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs), [File: crypto/frost/src/nonce.rs](crypto/frost/src/nonce.rs))

### Summary
Analogous to CVE-2023-43898 (a null-pointer dereference on a crafted input reaching an unchecked code path), `Commitments::read` accepts attacker-supplied commitment points — including the identity — with no rejection of degenerate values. The aggregate nonce `R = D + rho * E` summed over all participants can therefore be driven to the point at infinity by a participant who submits their preprocess last. During `sign()`, `IetfSchnorr::sign_share` computes `H::hram(&nonce_sums[0][0], ...)`, and the Bitcoin `Hram` calls `x(R)` which executes `expect("point at infinity")` — a panic reachable purely from untrusted preprocess bytes, aborting every honest signer that calls `sign`.

### Finding Description
`GeneratorCommitments::read` reads two points via `read_G` with no check that they are non-identity (`crypto/frost/src/nonce.rs:34-36`). `read_G` accepts the canonical encoding of the identity (`crypto/ciphersuite/src/lib.rs:91-100`). `BindingFactor::nonces` computes each session nonce as `Σ_l (D_l + rho_l * E_l)` over all included participants (`nonce.rs:194-212`), with no identity check on the result. `Schnorr::sign_share` passes `nonce_sums[0][0]` straight into `H::hram` (`crypto/frost/src/algorithm.rs:208`). For the Bitcoin ciphersuite, `Hram::hram` calls `x(R)` which does `encoded.x().expect("point at infinity")` (`networks/bitcoin/src/crypto.rs:13-22`).

### Impact Explanation
A participant (or anyone whose `read_preprocess` output is accepted by a signer) sends `D = -Σ_l(D_l + rho_l·E_l)`, `E = identity` as their preprocess. The aggregate `R` becomes the point at infinity, and every honest signer calling `sign()` panics inside `hram` — a remotely triggered denial of service of the signing protocol, mirroring the null-deref DoS of the external report.

### Likelihood Explanation
The binding factor `rho` is a deterministic transcript over `group_key`, `hash_msg`, and all preprocesses, so a participant who observes the other preprocesses before publishing their own can compute all `rho_l` and solve for a cancelling `D` trivially (set `E = identity`). Nothing in `read_preprocess` or `sign` rejects identity or cancelling commitments.

### Recommendation
In `Schnorr::sign_share` / `verify` (and the Bitcoin `Hram`), reject identity `R` and return an error/`None` instead of panicking; alternatively, validate in `Commitments::read` or `BindingFactor::nonces` that bound nonce contributions and the aggregate `R` are non-identity, faulting the offending participant rather than crashing.

### Proof of Concept
1. In a `t`-of-`n` Bitcoin Schnorr FROST session, collect honest preprocesses `(D_l, E_l)`.
2. Compute each `rho_l` from the `rho_transcript` (`sign.rs:362-371`), then publish preprocess `D = -(Σ_l D_l + Σ_l rho_l·E_l) + rho_self·0`-adjusted, `E = identity` so your bound contribution equals `-Σ_l(D_l + rho_l·E_l)`.
3. Any signer calling `sign()` computes `nonce_sums[0][0] = identity` and panics at `expect("point at infinity")` in `x()` (`crypto.rs:15`).