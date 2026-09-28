### Title
Deserialized `ThresholdKeys` accept a secret share inconsistent with the participant's verification share, causing self-incriminating invalid signature shares - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reconstructs a full signing key set from attacker-controlled bytes: `t`, `n`, `i`, the interpolation variant, `secret_share`, and `n` verification shares. It delegates validation to `ThresholdKeys::new`, which only checks the count of verification shares, that participant indexes are `<= n`, and that `Interpolation::Constant` is only used when `t == n` (crypto/dkg/src/lib.rs:355-374). It never verifies that `secret_share * G == verification_shares[i]`, nor that the verification shares correspond to any polynomial consistent with `secret_share`. A maliciously crafted serialized key blob — analogous to the malicious `.xml` file in CVE-2017-1000140 that is "executed" on load — is accepted and then *used* as live key material.

### Finding Description
- `ThresholdKeys::read` parses the interpolation tag: `0` reads `n` scalars as `Interpolation::Constant`, `1` is `Lagrange` (crypto/dkg/src/lib.rs:604-616), then reads `secret_share` and `n` points via `C::read_G` (which rejects non-canonical/invalid points but not semantically wrong ones) (crypto/dkg/src/lib.rs:618-623).
- `ThresholdKeys::new` derives `group_key` solely from `verification_shares[1..=t]` interpolated (crypto/dkg/src/lib.rs:376-378). The supplied `secret_share` is stored unconditionally (crypto/dkg/src/lib.rs:380-390).
- In `view()`, the secret share is interpolated and combined with the ephemeral offset added to `included[0]` (crypto/dkg/src/lib.rs:494-521). The resulting `ThresholdView` pairs the attacker's `secret_share` with the attacker's `verification_shares` map.
- During signing (`AlgorithmSignMachine::sign`, crypto/frost/src/sign.rs:398) the share is computed from that secret share. In `complete()` (crypto/frost/src/sign.rs:447-495), if the aggregate signature fails, `verify_share` is run per participant against `self.view.verification_share(*l)` — a value derived from the attacker-supplied `verification_shares`, not from anything bound to `secret_share`.

Two consequences follow:

1. If `secret_share * G != verification_shares[i]`, every share the victim emits fails its own share verification, so `FrostError::InvalidShare(i)` names the victim as the faulty signer (crypto/frost/src/sign.rs:476-484). On Serai's coordinator this is exactly the evidence used for slash reports — a crafted key file causes the victim to produce self-incriminating "provably invalid" shares.
2. The `group_key` is entirely attacker-defined via `verification_shares`, so the node participates in sessions and produces shares under a group key that bears no relation to the real DKG output, while `original_verification_shares`/blame data remain attacker-controlled.

### Impact Explanation
An unprivileged party who can get a crafted serialized `ThresholdKeys` blob loaded (the `ThresholdKeys::read` interface is one of the designated untrusted-input surfaces) can either (a) force the victim to emit signature shares that verifiably fail `verify_share` against the very `verification_shares` the attacker chose, producing a blame attribution against the victim and enabling a wrongful slash/liveliness failure of threshold signing, or (b) pin the node's `group_key` to an attacker-chosen value, silently diverging the node's view of the multisig identity. This is a semantic-deserialization flaw: bytes of one interpretation (a consistent key set) are honored while encoding a different reality, the same class as CVE-2017-1000140's file that is treated as executable content.

### Likelihood Explanation
Medium. Exploitation requires the crafted bytes to reach `ThresholdKeys::read` on a victim node — the interface exists precisely for loading untrusted serialized key material, but practical delivery depends on deployment (e.g., recovery/provisioning paths feeding externally supplied bytes). Once loaded, the failure is deterministic: every subsequent signing session produces invalid, self-blaming shares.

### Recommendation
In `ThresholdKeys::new` (or at the end of `ThresholdKeys::read`), validate consistency: assert `C::generator() * *secret_share == verification_shares[&params.i()]` (accounting for `scalar`/`offset` being applied later, compare against the untweaked share). For `Interpolation::Constant`, additionally verify the coefficient list length equals `t` and that each `verification_shares[l]` equals the evaluation of the constant polynomial at `l`, so a deserialized key set is provably self-consistent before any share is ever signed.

### Proof of Concept
```
// Attacker crafts bytes for ThresholdKeys::<Ed25519>::read:
//   id_len = 12, id = "edwards25519"
//   t = 2, n = 3, i = 1
//   interpolation = 1 (Lagrange)
//   secret_share = s_A (arbitrary scalar)
//   verification_shares = {1: V_B, 2: V_2, 3: V_3} where V_B != s_A * G
//
// ThresholdKeys::read -> ThresholdKeys::new succeeds: all checks pass.
let keys = ThresholdKeys::<Ed25519>::read(&mut crafted.as_slice()).unwrap();

// Victim loads keys and joins FROST signing as participant 1.
let machine = AlgorithmMachine::<Ed25519, SchnorrAlgorithm<_>>::new(alg, keys);
let (sign_machine, preprocess) = machine.preprocess(&mut OsRng);
// ... exchange preprocesses with peers ...
let (sig_machine, share) = sign_machine.sign(preprocesses, msg).unwrap();

// share = lambda_1 * s_A + nonce terms — but peers/victim verify it
// against verification_shares[1] = V_B != s_A * G.
// In complete(): aggregate sig fails, verify_share(1) fails,
// FrostError::InvalidShare(1) is returned — blaming the victim.
```

*Uncertainty noted:* I verified the missing consistency check in `ThresholdKeys::new`/`read` and the share/blame flow in `crypto/frost/src/sign.rs`. I did not fully trace whether a production Serai path feeds unauthenticated bytes into `ThresholdKeys::read` (it is listed as an untrusted-input surface for this scan); if the only caller is trusted local storage, the reachable-input premise weakens and this reduces to a hardening gap.