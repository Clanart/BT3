### Title
FROST Schnorr signing for Bitcoin panics (node crash) when a malicious participant's preprocess makes the aggregate nonce `R` the point at infinity - (File: networks/bitcoin/src/crypto.rs)

### Summary
`Hram::hram` computes `x(R)` / `x(A)`, which `expect`s a non-infinity point. `x()` panics on the point at infinity (`crypto.rs:13-16`, doc comments at `crypto.rs:54` and `crypto.rs:78-83` acknowledge this). A signer in the FROST set controls their preprocess `Commitments` (pairs `[D_i, E_i]` read via `read_preprocess` → `Commitments::read` → `NonceCommitments::read` → `GeneratorCommitments::read` → `C::read_G`, `crypto/frost/src/nonce.rs:34-35,74-80,133-139`), and `read_G` accepts the identity encoding. Because the binding factor `rho` is a public transcript function of the submitted commitments (`BindingFactor::calculate_binding_factors`, `crypto/frost/src/nonce.rs:161-173`), the attacker can solve `E_i = -rho⁻¹ · D_i` so their bound nonce `D_i + rho·E_i` (`nonce.rs:180-191`) is identity. Choosing all other parties' contributions as known, the aggregate `R` (`B.nonces`, `nonce.rs:194-212`) becomes the point at infinity. When `verify`/`sign_share` in `IetfSchnorr` computes the challenge via `Hram::hram(R, A, m)` (`crypto.rs:59-73`), `x(R)` panics, crashing the signing process. This is the Serai analog of CVE-2023-21867: network-reachable, low-complexity input causing a repeatable crash (complete availability loss of the signer).

### Finding Description
`crypto.rs` wraps the panic-prone path with only doc warnings:

- `x()` uses `encoded.x().expect("point at infinity")` (`crypto.rs:15`).
- `x_only()` `expect`s non-infinity/non-odd (`crypto.rs:22`).
- `Hram::hram` unconditionally calls `x(R)` and `x(A)` (`crypto.rs:65-66`).

`AlgorithmSignMachine::sign` never rejects identity commitments or an identity aggregate nonce. It transcripts each participant's commitments, computes `rho`, builds bound nonces (`sign.rs:324-396`), and calls `self.params.algorithm.sign_share(&view, &Rs, nonces, msg)` (`sign.rs:398`) where `Rs` may contain the identity point. `AlgorithmSignatureMachine::complete` likewise calls `algorithm.verify(group_key, &Rs, sum)` (`sign.rs:465`) with attacker-influenced `Rs`. The coordinator/processor feed peer-supplied bytes into `read_preprocess` (`coordinator/src/tributary/signing_protocol.rs:165`, `processor/src/batch_signer.rs:242`), so the malicious bytes reach `sign` directly.

### Impact Explanation
Any participant in a Bitcoin-threshold signing session (or anyone able to inject a preprocess message for a participant index already included in the set) can force every honest signer that calls `sign`/`complete` to panic inside `hram`. `FrostError` cannot be returned — the panic unwinds the thread, killing the signing attempt and, depending on runtime, the processor/coordinator task. Since preprocesses are per-attempt, the attacker can repeat this each attempt, giving a sustained DoS of Bitcoin signing (funds unspendable while the panic persists). It also bypasses blame: the crash happens before `InvalidPreprocess`/`InvalidShare` attribution, so the faulty validator is not identified or slashed.

### Likelihood Explanation
Requires only the ability to submit a preprocess to a signing set (a validator in the active set or a compromised share of preprocess bytes — no key material). The malicious `E_i` requires solving `rho·E_i = -D_i` where `rho` is a deterministic hash the attacker computes locally; cost is one hash plus a scalar inversion. Preprocess parsing accepts identity points since `read_G` only checks encoding validity. High feasibility for an in-set signer; Medium severity per the DoS-only impact.

### Recommendation
- In `AlgorithmSignMachine::sign`, after computing `Rs` via `B.nonces`, reject (return `FrostError::InvalidPreprocess(l)`) if any bound nonce or aggregate nonce is the point at infinity before calling `sign_share`/`verify`.
- Alternatively/additionally, reject identity `D`/`E` commitments in `GeneratorCommitments::read`/`NonceCommitments::read`, or have `BindingFactor::bound`/`nonces` return a `Result`.
- Make `x`/`x_only`/`hram` return `Option`/`io::Result` instead of panicking, so even unanticipated identity inputs are a signature-verification failure, not a crash.

### Proof of Concept
Sketch for a `Secp256k1` FROST session using `bitcoin_serai::crypto::Schnorr`:

1. Attacker is participant `l` in `included`. Honest preprocesses `B_l' = {D_l', E_l'}` are known (broadcast round).
2. Attacker chooses `D_l` arbitrary (e.g., generator). They compute the `rho` transcript exactly as `sign.rs:362-368` does: `group_key`, `hash_msg(msg)`, `preprocesses` challenge over the serialized participant/commitment transcript — all computable offline since it only depends on public preprocess bytes.
3. `rho_l = C::hash_binding_factor(transcript.challenge(b"rho"))`. Set `E_l = -rho_l⁻¹ · D_l`. Then bound nonce `D_l + rho_l·E_l = identity` (`nonce.rs:187`).
4. If needed to zero the full aggregate, choose `D_l` such that `sum of D terms + multiexp = identity` too (they control both scalars); simplest: pick `D_l = -(Σ other D + Σ rho·E)` and `E_l = -rho_l⁻¹ D_l`, making `R[n][g] = identity` in `B.nonces` (`nonce.rs:208`).
5. Serialize `Commitments { nonces: [NonceCommitments { generators: [(D_l, E_l)] }] }` and send as the preprocess. `read_preprocess` succeeds (`read_G` accepts these encodings).
6. Honest signer calls `machine.sign(preprocesses, msg)` → `Rs[0][0] = identity` → `Schnorr::verify`/`sign_share` → `Hram::hram(&identity, &A, m)` → `x(&identity)` hits `.expect("point at infinity")` → panic → signing thread dies before any `InvalidParticipant` blame is emitted.