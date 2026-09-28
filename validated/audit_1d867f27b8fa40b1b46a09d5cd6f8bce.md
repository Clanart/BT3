### Title
Deserialized `ThresholdKeys` fully attacker-controlled: no binding between secret share, verification shares, or group key — crafted key file yields an attacker-owned group key - (File: crypto/dkg/src/lib.rs)

### Summary
The mongosh advisory (CVE-2025-1756, CWE-426) concerns a crafted file placed in a searched location being loaded by a privileged component, causing it to act with elevated trust on attacker-supplied content. The direct analog in Serai is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`: a serialized key blob is loaded wholesale from a reader, and the resulting group key — the identity under which the node receives funds and produces FROST signatures — is computed entirely from attacker-supplied bytes, with no consistency check binding the secret share to the verification shares or the group key to any prior DKG transcript.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation method, `secret_share`, and `n` verification shares from raw bytes, then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs:574-631). `ThresholdKeys::new` only checks that the share count equals `n` and participant indexes are `<= n` (lines 355-365), then computes `group_key` by interpolating the verification shares of participants `1..=t` (lines 376-378).

Critically, it never verifies:

1. **`secret_share` ↔ `verification_shares[i]` consistency.** There is no check that `C::generator() * secret_share == verification_shares[&i]`. The private half and public half of the key are fully independent fields.
2. **`group_key` ↔ any external commitment.** No transcript hash, DKG session ID, or expected group key is stored or verified; `group_key` is whatever `Σ V_l · λ_l` evaluates to over the supplied shares.
3. **Non-identity of verification shares.** Shares are read via `<C as Ciphersuite>::read_G` (line 622), which — unlike `Curve::read_G` in crypto/frost/src/curve/mod.rs:125-131 — does not reject the identity point.
4. **Shares beyond `1..=t` are unused for `group_key`** under Lagrange interpolation, so the effective public key is determined solely by the first `t` attacker-controlled points.

An attacker who can place a crafted serialized-keys blob where the node loads it — the exact CWE-426 shape of the mongosh bug — produces a `ThresholdKeys` whose `group_key()` is a key the attacker fully controls (choose discrete log `a`, set `V_1..V_t` so `Σ V_l·λ_l = a·G`), while the node treats it as the legitimate threshold wallet.

### Impact Explanation
Whatever consumes `group_key()` — e.g., bitcoin-serai deriving the deposit address/script_pubkey from the group key — will attribute funds to a key whose secret the attacker knows alone. Funds "received" by the threshold wallet are spendable only by the attacker, and any FROST signing the node performs operates under the attacker's chosen group identity. Additionally, with identity verification shares accepted, a crafted blob can encode `V_j = 0` for some participants, corrupting share-verification math (a valid signature share `z_j` would need `z_j·G = R_j + c·λ_j·0`, decoupling it from any real secret). This is "funds reported received that are not spendable [by the intended group]" plus a forged key-binding, both accepted impact classes.

### Likelihood Explanation
Exploitation requires write access to wherever the serialized `ThresholdKeys` blob is stored/read — a local precondition, matching the original CVE's `AV:L/AC:H/PR:L` profile. The cryptography itself requires no break: the attacker freely picks all fields. The lack of any integrity tag, DKG-transcript commitment, or self-consistency check in `ThresholdKeys::read` means the crafted file always parses successfully.

### Recommendation
- In `ThresholdKeys::new`, assert `C::generator() * secret_share == verification_shares[&params.i()]`.
- Reject identity verification shares (use `Curve::read_G`-equivalent checks in `read`, or validate in `new`).
- Persist and verify a commitment to the DKG session (e.g., hash of the commitments transcript / expected group key) inside the serialization, or verify the deserialized `group_key` against an externally pinned value.
- Have all nodes that reconstruct keys cross-check `group_key` equality before use.

### Proof of Concept
For a target `t-of-n` Ristretto wallet with Lagrange interpolation, attacker picks scalar `a` and computes Lagrange coefficients `λ_l` for the set `{1..t}` (`Interpolation::interpolation_factor`, crypto/dkg/src/lib.rs:226-249). Set `V_l = (a·λ_l^{-1})·G` for `l ≤ t` and arbitrary points for `l > t`. Then `group_key = Σ V_l·λ_l = a·G`. Serialize `t, n, i=1`, interpolation tag `1`, any nonzero `secret_share`, and `V_1..V_n`; `ThresholdKeys::read` accepts it with no error and `group_key()` returns `a·G`, which the attacker alone can sign for.