### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept a secret share inconsistent with the verification shares, producing keys whose group key is unspendable - (File: crypto/dkg/src/lib.rs)

### Summary
The Neutron bug class is "missing validation of an input field at creation time produces an object in an inconsistent state that later causes a denial of service." The Serai analog lives in `crypto/dkg/src/lib.rs`: `ThresholdKeys::new` validates the *shape* of its inputs (participant indexes in range, `t <= n`, count of verification shares) but never validates that the supplied `secret_share` actually corresponds to `verification_shares[params.i]`. Because `ThresholdKeys::read` (an explicit read API in scope) reconstructs keys purely from attacker-controlled bytes and funnels straight into `ThresholdKeys::new`, malformed-but-well-formed-encoded keys are accepted.

### Finding Description
`ThresholdKeys::new` performs these checks only:
- `verification_shares.len() == n` and every key `<= n` (`crypto/dkg/src/lib.rs:355-365`), which with `Participant` being non-zero forces the key set to be exactly `1..=n`,
- `Interpolation::Constant` requires `t == n` (`crypto/dkg/src/lib.rs:367-374`).

It then derives `group_key` solely from `verification_shares` of participants `1..=t` (`crypto/dkg/src/lib.rs:376-378`). Nowhere is `C::generator() * secret_share == verification_shares[params.i]` checked. `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, `i`, the interpolation vector, `secret_share`, and `n` verification shares from the byte stream and calls `ThresholdKeys::new`, so an inconsistent `(secret_share, verification_shares[i])` pair deserializes successfully. This contrasts with the PedPoP flow, where `calculate_share` explicitly verifies each share against the commitments via `share_verification_statements` before accepting it (`crypto/dkg/pedpop/src/lib.rs:487-499`) — the invariant is required by the protocol but not enforced on the deserialization path.

### Impact Explanation
A node that loads `ThresholdKeys` from untrusted/serialized input accepts a key share that does not belong to the reported `group_key`. Any later `view()`/`sign` produces signature shares that fail FROST share verification (verifiers compute `s_i·G == R_i + c·verification_share_i`, which cannot hold for an arbitrary `secret_share`), so the participant can never produce a valid contribution — a persistent denial of service of that signer rather than a transient fault, since the corruption survives reserialization. More concretely for the fund-handling side: the group key reported by `group_key()` is derived entirely from attacker-chosen verification shares, so systems that key reception/scanning off the group key can report funds under a key for which no valid secret share exists — funds reported received that are not spendable.

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization API accepting arbitrary bytes; any path where key material is synced, backed up, transmitted, or reconstructed from serialized form reaches it. No cryptographic computation is needed by the attacker — they just encode a valid `t/n/i`, interpolation tag, a random `secret_share`, and `n` canonical points. Exploitation requires an integrator to accept serialized keys from a less-trusted source, which is plausible for backup/sync flows but not a purely remote zero-interaction trigger, keeping likelihood below certain.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), enforce share consistency: reject when `C::generator() * secret_share != verification_shares[params.i()]`. Optionally also reject identity/zero secret shares and identity verification shares (use `Curve`-style identity rejection where applicable). This makes the deserialized state machine identical in invariants to the one produced by the DKG protocols.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs context
// 1. Build a valid serialized ThresholdKeys blob, then replace the secret_share
//    field with an arbitrary scalar that does not match verification_shares[i].
//    read() succeeds because only format-level validation occurs:
//      - ThresholdParams::new(t, n, i) checks t<=n, i<=n         (lib.rs:166-179)
//      - read enforces verification_shares count == n and keys <= n
//        via ThresholdKeys::new                                   (lib.rs:355-365)
//      - NO check: generator() * secret_share == verification_shares[i]
// 2. keys.group_key() returns a key derived from the attacker-supplied
//    verification_shares (lib.rs:376-378), unrelated to secret_share.
// 3. keys.view(included).sign(...) emits shares failing FROST share
//    verification -> permanent DoS; funds sent to group_key() are unspendable.
```
Relevant code: `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`), `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), contrast with `share_verification_statements` usage in `crypto/dkg/pedpop/src/lib.rs:487-491`.