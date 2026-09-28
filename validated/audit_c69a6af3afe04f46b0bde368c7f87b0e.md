### Title
Attacker-controlled preprocess commitments force aggregate nonce `R` to the identity point, panicking the BIP-340 signer — a reachable, deterministic DoS — ([File: crypto/frost/src/nonce.rs], [File: networks/bitcoin/src/crypto.rs])

### Summary
CVE-2017-3641 is a "malformed input → complete crash/hang" class bug. The analog: `Commitments::read` accepts the point at infinity (it is a canonical encoding, so `Ciphersuite::read_G` passes it) and never rejects identity commitments. During `AlgorithmSignMachine::sign`, `BindingFactor::nonces` sums `D_l + rho_l * E_l` over all participants with no identity check, and the resulting `Rs[0][0]` is passed to `Hram::hram`, which calls `x()`/`x_only()`, panicking via `expect("point at infinity")` when `R` is the identity. A signer (or anyone able to submit preprocess bytes to `read_preprocess`/`sign`) can deterministically force `R = identity` and crash the signing process.

### Finding Description
- `GeneratorCommitments::read` / `NonceCommitments::read` / `Commitments::read` (crypto/frost/src/nonce.rs:34-139) read raw `read_G` points with no non-identity check; `read_G` only enforces canonical encoding (crypto/ciphersuite/src/lib.rs:91-101), which the identity point satisfies.
- `BindingFactor::nonces` (nonce.rs:194-211) computes `D = sum(D_l) + multiexp(rho_l, E_l)` with no identity rejection.
- `AlgorithmSignMachine::sign` (crypto/frost/src/sign.rs:283-398) calls `B.nonces(&nonces)` → `self.params.algorithm.sign_share(&view, &Rs, ...)`.
- For Bitcoin, `Schnorr::sign_share` → `Hram::hram(R, A, m)` (crypto.rs:59-73) calls `x(R)` (crypto.rs:13-16), which does `.expect("point at infinity")` — a panic. The docs even acknowledge `hram` panics on infinity `R` or `A` (crypto.rs:54).
- The attacker can force `R = identity` deterministically: rho values are public functions of the transcript (`calculate_binding_factors`, nonce.rs:161-173 — built from `group_key`, `hash_msg`, and the preprocesses challenge, all known before the attacker commits, since `sign` receives all preprocesses at once). With `rho_a != 0`, the attacker sets `E_a = G` and `D_a = -(sum_{l != a}(D_l + rho_l*E_l) + rho_a*E_a)`, making the aggregate `R` identity. If `rho_a = 0` (negligible probability), pick a different ordering/commitment or let `D_a` alone cancel when their `D` term isn't multiplied by rho (use `D_a = -rest` with `E_a` arbitrary when rho_a = 0 — rho_a = 0 also kills their rho term, so `D_a = -(sum_{l != a} ...)` suffices).
- Reachability: untrusted preprocess bytes are fed to `read_preprocess` → `Commitments::read` → then `sign(...)` by honest signers (as in processor/src/batch_signer.rs:241-272, which calls `machine.sign(preprocesses, ...)` on attacker-supplied preprocesses). The panic aborts the signing routine — complete DoS of that signing attempt, matching the CVE's "hang or repeatable crash" class.

### Impact Explanation
A single malicious preprocess message crashes every honest signer that processes it (panic inside `sign_share`), and is trivially repeatable — the attacker can poison every signing attempt, halting threshold signing for the Bitcoin network indefinitely. No key material is required; only the ability to submit preprocess bytes, which any participant in the set (or a relayed message under a claimed participant index ≤ n, since `sign` only bounds-checks `included.last() <= n` at sign.rs:302) can do.

### Likelihood Explanation
Deterministic: the binding factors are computable from public data before the attacker finalizes their commitment, and forcing a point sum to identity is trivial. The panic path is guaranteed (`expect` on infinity `x()` coordinate). Requires only the attacker's preprocess to be included in a signing set, which is normal protocol flow.

### Recommendation
Reject identity commitment points in `GeneratorCommitments::read`/`Commitments::read` (nonce.rs:34), and/or check the aggregate `Rs`/`bound()` results for identity in `sign`/`complete` and return `FrostError::InvalidPreprocess(l)` instead of panicking. Make `x()`/`x_only()`/`Hram::hram` (crypto.rs:13-23, 59) return `Option`/`io::Result` rather than `expect`ing non-infinity input.

### Proof of Concept
1. Honest signers 1..=t begin a Bitcoin `Schnorr` FROST session; attacker is participant `a` (or spoofs preprocess bytes for an index in the set).
2. Attacker collects/anticipates all other preprocesses `{D_l, E_l}`, computes each `rho_l` via the public `rho_transcript` (sign.rs:361-371, nonce.rs:161-173).
3. Attacker crafts a preprocess whose `GeneratorCommitments` are `E_a = G`, `D_a = -(sum_{l!=a}(D_l + rho_l*E_l)) - rho_a*G`, serializes it (identity-containing or arbitrary points, all canonical) and submits it.
4. Each honest signer calls `read_preprocess` (accepted — `read_G` passes canonical points, nonce.rs:34-36) then `sign` → `B.nonces` yields `R = identity` → `Hram::hram` → `x(R)` → `expect("point at infinity")` panics (crypto.rs:15), crashing the signing process. Repeats on every attempt.