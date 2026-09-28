### Title
Crafted FROST preprocess commitments force the aggregate nonce to the identity point, panicking BIP-340 `Hram::hram` via `x()` on `ProjectivePoint` infinity — ([File: networks/bitcoin/src/crypto.rs])

### Summary
CVE-2023-21913 is an availability bug: a remotely-reachable input causes a repeatable crash/DoS. The Serai analog is in `networks/bitcoin/src/crypto.rs`, where `x(key)` unconditionally calls `encoded.x().expect("point at infinity")` and `x_only` does the same. The `Hram::hram` implementation calls `x(R)` and `x(A)` on `ProjectivePoint` values that are computed, not deserialized — specifically `R` is the per-participant aggregate nonce `D + Σ rho·E` produced by `BindingFactor::nonces` in `crypto/frost/src/nonce.rs`. Although `Curve::read_G` rejects identity points on the wire, nothing prevents a signing participant from choosing preprocess commitments `D, E` such that the *bound sum* collapses to the identity. The code itself documents this: `Schnorr` "may panic if called with nonces/a group key which are the point at infinity".

### Finding Description
`BindingFactor::bound`/`nonces` (crypto/frost/src/nonce.rs:180-212) computes the aggregate nonce as `D + multiexp_vartime(statements)` over attacker-controlled commitment points. An attacker who knows the binding factor derivation (public transcript) can craft `E` such that `rho * E = -D - Σ(other contributions)`, yielding `nonces[n][g] == identity`. `read_preprocess`/`Commitments::read` only calls `C::read_G` per commitment — non-identity commitments are required, but their *sum* is never checked.

That nonce is then passed to `Algorithm::verify`/`sign_share` inside `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:465) and to `IetfSchnorr::sign_share`, which evaluates `Hram::hram(&R, &A, msg)`. `hram` calls `x(R)` at crypto.rs:65, which executes `.expect("point at infinity")` at crypto.rs:15 — a panic that aborts the host process. `x_only` at crypto.rs:22 has an identical panic, reachable wherever the tweaked/group key or nonce is fed to `x_only` (e.g., output/address derivation paths in `wallet/`).

Reachability: any counterparty able to supply a FROST `Preprocess` message — i.e., untrusted bytes consumed by `read_preprocess` → `Commitments::read` → `NonceCommitments::read` — then triggers the panic when the honest signer calls `sign`/`complete`. This satisfies the "untrusted bytes fed to `read_preprocess` / `complete`" reachability rule; no key material or validator collusion is needed beyond being a participant in one signing session.

### Impact Explanation
A single crafted preprocess message deterministically panics the victim's signing process during `sign`/`complete` — a repeatable, remotely-triggered crash of the Bitcoin signing path, matching the CVE's "complete DOS" impact. Because the panic occurs inside `verify` before share-level blame can be assigned, the attacker also avoids attribution via the `InvalidShare` path.

### Likelihood Explanation
The attacker controls their own commitment bytes and knows all other participants' preprocesses (broadcast on the signing channel) plus the public rho transcript, so solving `rho·E = −(D + Σ_j rho_j·E_j + D_j)` is one inversion in the scalar field — trivial. The library explicitly acknowledges the panic ("MAY panic", "Panics on invalid input"), confirming the reachable code path exists rather than being defensive.

### Recommendation
In `networks/bitcoin/src/crypto.rs`, make `x`/`x_only` fallible (`Option`/`io::Result`) and propagate, or in `BindingFactor::nonces`/`frost` verify path, reject aggregate nonces equal to the identity (and the identity group key) before invoking `Hram::hram`. Also check the same identity-sum condition in `sign_share` for `A`/`R` before `x()`/`x_only()` are called.

### Proof of Concept
1. Victim runs FROST signing via `bitcoin_serai::crypto::Schnorr` with attacker as one participant.
2. Victim calls `read_preprocess` on the attacker's bytes; `Commitments::read` accepts non-identity `D_a, E_a`.
3. Attacker sets `E_a = rho^{-1}·(−D_a − Σ_{others}(D_j + rho_j·E_j))` so `B.nonces()[0][0] == identity` (crypto/frost/src/nonce.rs:194-211).
4. Victim calls `complete(shares)` → `Schnorr::verify(group_key, &Rs, sum)` → `Hram::hram(&identity_R, &A, msg)` → `x(R)` → `expect("point at infinity")` panics (crypto.rs:13-16, 65).

Note: I could not exhaustively verify every caller of `x_only` in `wallet/` within available iterations, so there may be additional panic sites via tweaked-key derivation; the `hram`/`verify` path above is fully supported by the cited code.