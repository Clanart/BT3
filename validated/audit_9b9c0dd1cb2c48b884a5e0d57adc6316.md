### Title
Attacker-Controlled Serialized `ThresholdKeys` Sets an Unvalidated Group Key and Inconsistent Secret Share, Enabling Signing Under an Attacker-Chosen Key - (File: crypto/dkg/src/lib.rs)

### Summary
The report's bug class is a privileged component consuming an attacker-supplied, unvalidated input (the `overrides.yoke.cd/flight` URL) and executing the attacker-selected object. The Serai analog is `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`): it deserializes `t`, `n`, `i`, an interpolation mode, a `secret_share`, and `n` verification shares purely from the untrusted byte stream, and `ThresholdKeys::new` derives the group key as `Σ verification_shares[1..=t] · λ_i` — never checking that the signer's own `secret_share` is consistent with `verification_shares[i]` and never committing the remaining `n - t` verification shares to the group key. The signing code itself acknowledges this hole: `AlgorithmSignatureMachine::complete` returns `FrostError::InternalError("everyone had a valid share yet the signature was still invalid")` with the comment that "the only known way to cause this … is to deserialize a semantically invalid FrostKeys" (`crypto/frost/src/sign.rs:491-494`).

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`):

1. Reads `t`, `n`, `i` and validates only `ThresholdParams::new(t, n, i)` (i.e., `t <= n`, `0 < i <= n`) (`crypto/dkg/src/lib.rs:166-179`).
2. Reads `Interpolation::Constant(Vec<F>)` with `n` attacker-chosen coefficients, or `Lagrange` (`crypto/dkg/src/lib.rs:604-616`).
3. Reads `secret_share` — an arbitrary scalar (`crypto/dkg/src/lib.rs:618`).
4. Reads `n` verification shares, each only required to be a canonical point via `C::read_G` (`crypto/dkg/src/lib.rs:620-623`, `crypto/ciphersuite/src/lib.rs:91-100`), which notably does *not* reject the identity point (unlike `Curve::read_G` at `crypto/frost/src/curve/mod.rs:125-131`).
5. Computes `group_key` inside `ThresholdKeys::new` only from `verification_shares[1..=t]` (`crypto/dkg/src/lib.rs:376-378`).

There is no check anywhere that `C::generator() * secret_share == verification_shares[i]`, nor any check that the verification shares are consistent evaluations of a single polynomial. Downstream, `view()` interpolates `secret_share` with attacker-controlled `Constant` coefficients (or Lagrange over `included`) and adds `offset` to `included[0]` (`crypto/dkg/src/lib.rs:494-521`), and `sign()` emits a `SignatureShare` and contributes to `sum` which is then verified against the attacker-defined `view.group_key()` (`crypto/frost/src/sign.rs:398-466`).

### Impact Explanation
A party who can feed crafted bytes to `ThresholdKeys::read` (the key blobs are consumed by processors, e.g. `GeneratedKeysDb::read_keys` at `processor/src/key_gen.rs:47-62` calls `ThresholdKeys::read(...).unwrap()`) controls:

- **`group_key`**: fully determined by `verification_shares[1..=t]` and the interpolation mode — the attacker picks shares so the group key is any point of their choosing, including `G * x` for a known `x`.
- **`secret_share`**: set it equal to the attacker's known scalar with `verification_shares[i] = G * secret_share`, so every emitted share is "valid" — the node then produces real FROST signature shares over messages (`msg` comes from externally triggered plans) for a group key whose discrete log the attacker knows. This yields signatures under an attacker-chosen key with the node's participation, i.e., signing of unintended/attacker-framed key material, or a group key whose funds the attacker alone can move.
- **Misbinding/inconsistency**: with `verification_shares[i] != G * secret_share`, the node emits shares that fail batch verification against honest shares — honest signers get blamed via `FrostError::InvalidShare` (`crypto/frost/src/sign.rs:487-489`), or the signing session aborts as `InternalError`, corrupting blame attribution.

This is the direct analog of the advisory: an unchecked attacker-supplied value (serialized key material vs. WASM URL) is loaded and executed/acted upon by a privileged component without validating that it is the intended object.

### Likelihood Explanation
Reachable whenever serialized `ThresholdKeys` cross a trust boundary — the processor round-trips them through its database (`processor/src/key_gen.rs:47-84`) and re-reads them on every signing session, and the audit rules explicitly place `ThresholdKeys::read` among the sanctioned untrusted-byte sinks. Exploitation requires only crafting a blob offline (all fields are plain encodings parsed by `read_F`/`read_G`/`u16`s); no protocol participation or collusion is needed. Impact is signing under an attacker-known/attacker-chosen key or induced blame corruption — High severity within the allowed impact classes (unintended signing / unspendable or attacker-controlled funds).

### Recommendation
- In `ThresholdKeys::read`/`ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[params.i()]` and reject otherwise.
- Commit the group key derivation to *all* `n` verification shares (e.g., verify each share lies on the degree-`t-1` polynomial defined by the commitments, or require `t == n`-style consistency checks for `Constant` interpolation coefficients against the shares).
- Reject identity verification shares on read (use an identity-rejecting `read_G` as `Curve::read_G` does).
- Store/commit a transcript hash or explicit `group_key` field and verify it matches the recomputed value on deserialization.

### Proof of Concept
```rust
// Attacker crafts a ThresholdKeys blob for curve C with t = 2, n = 3, i = 1.
let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes());          // t
blob.extend(3u16.to_le_bytes());          // n
blob.extend(1u16.to_le_bytes());          // i = Participant(1)
blob.push(1);                             // Interpolation::Lagrange

// Secret share = attacker-known scalar x_1
let x_1 = /* attacker-chosen scalar */;
blob.extend(x_1.to_repr().as_ref());

// Verification shares: all consistent with a *single* polynomial the
// attacker fully knows (secret x with x_1 = p(1), x_2 = p(2), x_3 = p(3)),
// or with verification_shares[1..=t] chosen so group_key = G * x for known x.
for share in [G * x_1, G * x_2, G * x_3] {
    blob.extend(share.to_bytes().as_ref());
}

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// keys.group_key() == G * x — a key whose discrete log the attacker knows.
// The node will now happily produce valid FROST signature shares under this
// key for any message it is driven to sign.
// Alternatively: set verification_shares[1] != G * x_1 → the node's shares
// fail verify_share, causing InvalidShare blame against honest participants
// or InternalError at crypto/frost/src/sign.rs:494.
```