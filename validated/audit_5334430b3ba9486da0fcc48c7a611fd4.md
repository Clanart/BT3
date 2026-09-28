### Title
Crafted `ThresholdKeys` deserialize to an attacker-controlled group key, enabling forged signatures - ([File: crypto/dkg/src/lib.rs])

### Summary
Analogous to the Debt DAO finding — where a degenerate input (empty collateral) made `_getLatestCollateralRatio()` return `0` and flipped `EscrowedLoan` into the privileged `LIQUIDATABLE` state — `ThresholdKeys::read`/`ThresholdKeys::new` computes the security-critical `group_key` directly from attacker-supplied verification shares with no validity check. An unprivileged party feeding crafted bytes to `ThresholdKeys::read` obtains a key object whose `group_key()` has a discrete logarithm the attacker knows, and any FROST `Algorithm::verify` / `SchnorrSignature::verify` check against that group key accepts attacker-forged signatures.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)`, the interpolation method, a secret share, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:574-631`). `ThresholdKeys::new` only validates that the number of shares equals `n` and that indexes are `<= n` (`crypto/dkg/src/lib.rs:355-365`). It then derives the group key as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(`crypto/dkg/src/lib.rs:376-378`)

Because the Lagrange interpolation factors over a fixed set sum to 1, setting every verification share for participants `1..=t` to the same point `P` yields `group_key = P`. The attacker picks `P = k * G` for a known scalar `k`, making the resulting `group_key` a key for which they hold the discrete logarithm — the exact analog of "collateral ratio = 0 because collateral is empty": a degenerate-but-well-formed input produces a security-critical value (`LIQUIDATABLE` / attacker-known `group_key`) without any authorization.

Any code path that later treats `keys.group_key()` as the group's public key — e.g., `SchnorrSignature::verify(public_key, challenge)` (`crypto/schnorr/src/lib.rs:108-110`, checking `R + cA - sG == 0`), or FROST's `Algorithm::verify`/`AlgorithmSignatureMachine::complete` which verifies against `self.view.group_key()` (`crypto/frost/src/sign.rs:465`) — will accept signatures the attacker produces with `k`. The attacker can also simply set all shares to the identity point where `read_G` admits it, making `group_key` the identity and `(R = identity, s = 0)` a universally valid signature, since `batch_statements` reduces to `(1·id) + (c·id) - (0·G) = id` (`crypto/schnorr/src/lib.rs:92-99`).

### Impact Explanation
An unprivileged party who can cause `ThresholdKeys` bytes to be deserialized (listed in scope as an untrusted-bytes entry point) produces a key object whose group key they fully control. Any signature verification or downstream trust decision keyed on `group_key()` is forgeable — the same class of harm as the reference bug, where a malformed status enabled privileged, unauthorized operations (changing the spigot owner/split, sweeping tokens). Here the forged verification outcome is the unauthorized capability.

### Likelihood Explanation
Reachability requires a caller to accept `ThresholdKeys` (or derived group key) over an untrusted channel rather than from a completed DKG — the same trust boundary assumption the scan rules make for `ThresholdKeys::read`. Given such a path exists, the attack is deterministic: it requires only crafting `n` identical (or identity) canonical points plus any secret share, with no interactive participation, collusion, or honest-key leakage. The code performs no check that the shares are consistent with an unknown-dlog group key.

### Recommendation
Validate, in `ThresholdKeys::new`, that `group_key` is not the identity and (where feasible) that verification shares are not all identical; document that `ThresholdKeys::read` must only consume bytes authenticated as output of a legitimate DKG. Downstream verifiers should bind the expected `group_key` from a trusted source rather than from deserialized key material.

### Proof of Concept
```rust
// For C = Secp256k1, t = 1, n = 1, i = Participant(1)
let k = Scalar::random(&mut OsRng);          // attacker-chosen, known
let p = ProjectivePoint::GENERATOR * k;      // verification share == intended group key
// ThresholdKeys::read bytes: ID, t=1, n=1, i=1, Interpolation::Lagrange (1),
// secret_share = k (or anything), verification_shares[1] = P.to_bytes()
let keys = ThresholdKeys::<Secp256k1>::read(&mut crafted_bytes).unwrap();
assert_eq!(keys.group_key(), p);             // attacker knows dlog(k) of group key

// Forge: sign any message/challenge with k
let sig = SchnorrSignature::<Secp256k1>::sign(&k.into(), nonce, challenge);
assert!(sig.verify(keys.group_key(), challenge)); // passes — forged signature
```
The `t = 1 ..= t` subset sum over identical shares `P` equals `P` because Lagrange factors sum to 1, and `ThresholdKeys::new` never checks that the resulting `group_key` has an unknown discrete logarithm or is non-identity.

Caveat: the severity depends on `C::read_G` accepting the chosen points (canonical encodings are enforced, but identity acceptance varies by ciphersuite) and on a deployment feeding untrusted bytes to `ThresholdKeys::read`; the former is partially unverified in the available index, and the latter is the assumed trust boundary per the engagement rules.