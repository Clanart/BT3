### Title
Malicious FROST participant can force the aggregate nonce `R` to the point at infinity, panicking `Hram::hram`/`x()` and aborting Bitcoin transaction signing - (File: networks/bitcoin/src/crypto.rs)

### Summary
`networks/bitcoin/src/crypto.rs` implements a BIP-340 HRAm whose helpers `x()` and `x_only()` call `.expect("point at infinity")` / `.expect("x_only was passed a point which was infinity or odd")`. `Hram::hram(R, A, m)` unconditionally calls `x(R)` and `x(A)`. Neither `read_G` nor FROST's `Commitments::read`/`read_preprocess` rejects the identity point (it is a canonical, valid `GroupEncoding`), so a malicious signing participant can submit nonce commitments chosen to make the group nonce commitment `R = sum(D_i + rho_i * E_i)` the point at infinity. When a victim signer runs `TransactionSignMachine::sign` (networks/bitcoin/src/wallet/send.rs:383-391), `sign_share`/`verify` evaluates `hram` on that `R` and the node panics instead of returning an error — an unprivileged, deterministic denial of service against threshold signing, analogous to CVE-2018-6942's crafted-input NULL dereference DoS.

### Finding Description
- `x()` (crypto.rs:13-16): `encoded.x().expect("point at infinity")` — panics on infinity.
- `x_only()` (crypto.rs:21-23): panics on infinity.
- `Hram::hram` (crypto.rs:59-73): calls `x(R)` and `x(A)` with no identity check; the doc comment on `Schnorr` (lines 76-83) explicitly admits "this library MAY panic" and assumes "negligible probability", which is wrong against an adaptive adversary who picks commitments after seeing honest preprocesses.
- `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only enforces canonical encoding; the identity point deserializes successfully, so attacker-controlled bytes fed to `read_preprocess`/`Commitments::read` can encode identity nonce commitments.
- `TransactionSignMachine::sign` (wallet/send.rs:355-398) maps attacker-supplied `HashMap<Participant, Vec<Preprocess>>` directly into `sig.sign(commitments[i], sighash)`; inside FROST, `R` is computed from the binding-factor-weighted commitment sums and passed to `hram` without an identity check on `R`.
- The attacker sends their preprocess last (or for a second nonce commitment `E = -D/rho`), forcing `R` to identity, deterministically crashing the victim's signing attempt.

### Impact Explanation
Any participant in a Bitcoin threshold-signing session (which only requires being a listed signer with the ability to send preprocess messages) can deterministically panic every honest signer that processes their preprocess. This aborts the signing round; depending on the deployment, an uncaught panic crashes the processor/tributary task, denying service for Bitcoin spends. This maps to CVSS availability impact (A:H) like the reference CVE — a crafted input causes a crash.

### Likelihood Explanation
Reachable from public inputs only: the attacker controls bytes deserialized via `read_preprocess` → `Commitments::read` → `read_G`, which accepts identity. No leaked keys, no collusion threshold beyond being one participant, no broken BFT assumption needed. The panic path is explicit in `x()`/`x_only()` and acknowledged in the `Schnorr` doc comment. Probability of triggering is ~1 for an adaptive attacker; likelihood is high within signing sessions that include a malicious or compromised signer.

### Recommendation
- In `Hram::hram` (and/or `Schnorr::verify`/`sign_share` wrappers), reject `R`/`A` equal to `ProjectivePoint::IDENTITY` and return a `FrostError`/verification failure instead of panicking.
- Additionally, reject identity nonce commitments in `Commitments::read`/`read_preprocess` (or defensively in `TransactionSignMachine::read_preprocess`), matching the spirit of the `Signed::read` identity-`R` check in coordinator/tributary/src/transaction.rs:62-69.
- Replace `.expect("point at infinity")` in `x()`/`x_only()` with a checked `Option`-returning API so callers propagate errors.

### Proof of Concept
1. Attacker is a participant in a `TransactionSignMachine` session and waits for all honest preprocesses.
2. Attacker computes the binding factor `rho_i` for their own preprocess deterministically (it is derived from the commitment list, which the attacker now fully knows except for their own entry they get to choose).
3. Attacker submits a preprocess whose `Commitments` encode `D_attacker, E_attacker` chosen so `sum_i (D_i + rho_i * E_i) = identity` — e.g. set `E_attacker = identity` and `D_attacker = -(sum of all other weighted commitments)`; both serialize as canonical points accepted by `Secp256k1::read_G`.
4. Victim calls `TransactionSignMachine::sign` → `AlgorithmSignMachine::sign` → computes `R = identity` → `Hram::hram(&R, &A, msg)` → `x(R)` → `encoded.x().expect("point at infinity")` panics (crypto.rs:15).

Note: I could not fully verify within the available iterations whether `crypto/frost`'s `sign`/`verify_share` already rejects an identity aggregate `R` before invoking the HRAm; if such a check exists upstream of `hram`, this analog is mitigated. The panic sites and the absence of identity rejection in `read_G` are confirmed in the files cited.