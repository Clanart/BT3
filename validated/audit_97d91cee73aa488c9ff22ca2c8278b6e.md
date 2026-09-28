### Title
`ThresholdKeys::new`/`read` never bind `secret_share` to `verification_shares[i]` or constrain `Interpolation::Constant` coefficients, letting supplied key material grant unilateral ("root-equivalent") signing power - ([File: crypto/dkg/src/lib.rs])

### Summary
The OpenBao advisory is a privilege-elevation bug: an endpoint that legitimately accepts arbitrary "policies" failed to exclude the restricted `root` policy. The Serai analog lives in `crypto/dkg/src/lib.rs`: `ThresholdKeys::new` accepts an attacker-influenced `secret_share`, an attacker-influenced `verification_shares` map, and — for `Interpolation::Constant` — a fully attacker-chosen coefficient vector, computing `group_key` purely from those inputs without ever checking that `C::generator() * secret_share == verification_shares[params.i()]`, and `ThresholdKeys::read` will ingest all of it from untrusted bytes (`crypto/dkg/src/lib.rs:349-391`, `574-632`).

### Finding Description
`ThresholdKeys::new` performs only three checks: the count of verification shares equals `n` (`crypto/dkg/src/lib.rs:355-360`), no participant index exceeds `n` (`crypto/dkg/src/lib.rs:361-365`), and `Constant` is only used when `t == n` (`crypto/dkg/src/lib.rs:367-374`). It then derives `group_key` as a linear combination over `verification_shares[1..=t]` weighted by the caller-supplied interpolation coefficients (`crypto/dkg/src/lib.rs:376-378`) and stores the supplied `secret_share` with no consistency check against `verification_shares[i]`.

`ThresholdKeys::read` reconstructs this structure from raw bytes: it reads `t`, `n`, `i`, an interpolation tag where tag `0` reads `n` arbitrary field elements as `Constant` coefficients (`crypto/dkg/src/lib.rs:604-616`), a `secret_share` scalar (`crypto/dkg/src/lib.rs:618`), and `n` verification-share points (`crypto/dkg/src/lib.rs:620-623`), then calls `ThresholdKeys::new`. A malicious coordinator provisioning a node (exactly the OpenBao shape — a legitimately permissive interface missing a restriction on a power-granting parameter) can therefore hand it a `ThresholdKeys` blob whose effective group key is `G * x` for a scalar `x` the attacker knows: set every `verification_shares[j] = G * a_j`, pick `Constant` coefficients `c_j` with `sum(c_j * a_j) = x`, and give the victim any `secret_share`. The victim's `group_key()` (`crypto/dkg/src/lib.rs:445-447`) and `view()` then resolve to the attacker-controlled key, and FROST `AlgorithmSignMachine`/`sign`/`complete` (`crypto/frost/src/sign.rs:283-411`, `447-495`) will operate on that key. The reverse also holds: the attacker can set `verification_shares[i]` to a real group's share but `secret_share` to an unrelated value, and the inconsistency is only detected at signature-verification time (which hits `FrostError::InternalError`, per the comment at `crypto/frost/src/sign.rs:491-494` acknowledging "a semantically invalid FrostKeys" deserialized as the only known trigger).

### Impact Explanation
An unprivileged party able to supply the serialized key material a node loads (backup/restore, coordinator-distributed key files, imported shares) causes the node to adopt and sign under a group key the attacker fully controls — the direct analog of an operator elevating a token to the `root` policy. Signatures produced via `AlgorithmSignatureMachine::complete` validate under the attacker-chosen `group_key`, so funds controlled by that key are solely the attacker's, not threshold-protected. Additionally, a blob claiming membership in a real group but with a `secret_share` inconsistent with `verification_shares[i]` produces a node that signs invalid shares and either gets blamed or trips `InternalError` — a concrete integrity/availability impact on threshold signing.

### Likelihood Explanation
Requires the integrator to ingest `ThresholdKeys` bytes from a party that should only provision a *share* of an established group — a documented supported flow (`write`/`read`, `crypto/dkg/src/lib.rs:535-632`). Within that flow no cryptographic consistency check exists, so exploitation is deterministic once bytes are accepted. Reachability depends on integrator behavior, matching the OpenBao precondition of privileged-but-constrained access; severity is bounded accordingly (High/Medium rather than Critical).

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[&params.i()]` before constructing the core. For `Interpolation::Constant`, additionally verify the coefficients against a canonical derivation (as `musig()` does at `crypto/dkg/musig/src/lib.rs:139-161`, where binding factors are recomputed from context+keys rather than trusted) or refuse `Constant` on the untrusted `read` path. At minimum, document/enforce that the caller must compare `group_key()` against the expected group key after `read`.

### Proof of Concept
```rust
// Attacker crafts serialized ThresholdKeys for Ciphersuite C, n = t = 2.
// Attacker chooses scalars a1, a2, sets V_j = G * a_j, coefficients c = (1, 1),
// so group_key = V_1 + V_2 = G * (a1 + a2) — a key only the attacker knows.
// victim_blob.write layout (ThresholdKeys::read, crypto/dkg/src/lib.rs:574-632):
//   [id_len][C::ID][t=2][n=2][i=1][tag=0][c1][c2][secret_share][V_1][V_2]
let keys = ThresholdKeys::<C>::read(&mut attacker_blob).unwrap();
// group_key() == G * (a1 + a2): fully attacker-controlled.
assert_eq!(keys.group_key(), C::generator() * (a1 + a2));
// keys.params().t() == 2 presents it as a 2-of-2 threshold key,
// yet no threshold protects the group key. Node signs via
// AlgorithmSignMachine::sign/complete under the attacker's key.
```

The PoC stands on `crypto/dkg/src/lib.rs` alone: `ThresholdKeys::new` never evaluates `G * secret_share` nor verifies the coefficient vector, so both the controlled-group-key and inconsistent-share variants are accepted without error.