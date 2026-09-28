### Title
Zero-valued `Interpolation::Constant` coefficients let a participant count toward the FROST threshold while requiring no secret/signature — (`crypto/dkg/src/lib.rs`)

### Summary
The Symbiotic-relay bug class is a *null element counted toward quorum*: the circuit skips a `(0,0)` validator key when hashing/aggregating signatures, yet still counts its (arbitrary) voting power, so quorum is reached with no signature. The Serai analog lives in `ThresholdKeys`/`ThresholdView`: `Interpolation::Constant` coefficients are attacker-influenced scalars, and `ThresholdView::verification_share`/`secret_share` multiply each participant's material by `interpolation_factor(i) = c[i-1]`. A coefficient of `0` produces a participant who is counted in `included.len() >= t` yet whose verification share is the identity and whose secret-share contribution is nil — a "validator with voting power but a null key" that passes FROST share verification with only a nonce, no secret.

### Finding Description
`ThresholdKeys::read` deserializes `Interpolation::Constant` by reading `n` scalars via `C::read_F` with no nonzero/validity check (`crypto/dkg/src/lib.rs:607-613`). `ThresholdKeys::new` only requires `t == n` for `Constant` interpolation (`lib.rs:368-371`) and accepts the coefficients verbatim. `Interpolation::interpolation_factor` then returns `c[i-1]` directly for any participant (`lib.rs:228`).

When `ThresholdKeys::view` builds the signing set, it only enforces `included.len() >= t`, sorting, deduplication, and `<= n` (`lib.rs:463-491`). It then computes each signer's interpolated verification share as `verification_shares[i] * scalar * interpolation_factor(i, included)` (`lib.rs:500-507`) and the interpolated secret share the same way (`lib.rs:494-498`).

If `c[i-1] == 0` for participant `i`:
- `view.verification_share(i)` is the identity point (`lib.rs:503-506`), and
- the FROST per-signer equation `s_i·G == (D_i + ρ_i·E_i) + c·λ_i·Y_i` reduces to `s_i·G == R_i`, since `λ_i·Y_i = 0` — i.e., any party can produce a valid signature share for participant `i` knowing only the nonce commitment `R_i`, without any key share.

This exactly mirrors the Symbiotic `(0,0)` validator: the participant is *counted* toward the quorum (`included` length satisfies `t`), yet *skipped* cryptographically (zero factor ⇒ identity share ⇒ no signature required). An attacker who can supply the deserialized `ThresholdKeys` (e.g., `Interpolation::Constant([0, c2, …])` with only one nonzero coefficient) reduces a `t`-of-`n` FROST multisig to a single-signer forge under the same `group_key`, because `group_key = Σ_{1..t} c_i·Y_i` is itself computed only from the nonzero coefficients (`lib.rs:376-378`).

### Impact Explanation
Forged threshold signatures: the attacker satisfies `included.len() >= t` while only one participant's secret is actually needed. Any message signed under the group's FROST key (in Serai, the Tendermint-style validator sets / network key controlling funds) can be produced without quorum. This is the same "quorum reached by null members" compromise as the external report.

### Likelihood Explanation
Reachability requires feeding attacker-controlled bytes into `ThresholdKeys::read`/`ThresholdKeys::new` — an in-scope untrusted-input sink per scope rules, though keys are normally produced internally by the DKG, which lowers practical likelihood. The cryptographic flaw itself (no check that constant-interpolation coefficients are nonzero / consistent with a real threshold sharing) is unconditional once such bytes are accepted. Medium.

### Recommendation
In `ThresholdKeys::new` (and hence `read`), reject `Interpolation::Constant` vectors containing any zero coefficient, and ideally verify the coefficients form a consistent sharing (e.g., non-zero, and — where applicable — document that `Constant` must come from a trusted key-generation ceremony, not unverified bytes). Treat `ThresholdKeys::read` input as untrusted and validate semantics, not just encoding.

### Proof of Concept
1. Serialize `ThresholdKeys` bytes for `t == n = 2` with `Interpolation::Constant([0, 1])`, arbitrary `secret_share`, and `verification_shares = {1: identity, 2: Y2}` such that `group_key = 0·Y1 + 1·Y2 = Y2` (consistent with `lib.rs:376-378`).
2. A victim calls `ThresholdKeys::read` → `ThresholdKeys::new` accepts (`t == n`, `len == n`, all participants `≤ n`).
3. On `view([1, 2])`: participant 1's interpolated verification share is `identity` (`λ_1 = 0` via `interpolation_factor`, `lib.rs:228`), participant 2's is `Y2`.
4. In `frost/src/sign.rs`, the coalition produces a signature where participant 1's share `s_1` is generated knowing only the announced preprocess `R_1 = D_1 + ρ_1 E_1` (any nonce `d` works: `s_1 = d + ρ_1 e`), since `λ_1·Y_1 = 0` contributes nothing. The aggregate verifies under `group_key` while participant 1 — counted in the quorum — never possessed or used a secret share.

Files: `crypto/dkg/src/lib.rs` (`Interpolation::Constant` read at lines 604-613; `interpolation_factor` at 226-248; `ThresholdKeys::new` at 349-391; `view` at 463-533).