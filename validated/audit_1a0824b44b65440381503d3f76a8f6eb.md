### Title
`ThresholdKeys::read` / `ThresholdKeys::new` sum attacker-controlled verification shares into `group_key` without any non-identity or consistency check, permitting arbitrary group key injection - (File: crypto/dkg/src/lib.rs)

### Summary
The analog of the reported "`sum()` does not check for non-zero / attacker-controlled components" bug lives in `ThresholdKeys::new`. The group key is computed as a bare `.sum()` over interpolation-weighted verification shares, and `ThresholdKeys::read` deserializes those shares with `<C as Ciphersuite>::read_G` — the ciphersuite-level reader, not `Curve::read_G` which rejects identity (`crypto/frost/src/curve/mod.rs:125-131`). No check ensures any verification share is non-identity, that the summed `group_key` is non-identity, or that `group_key` is consistent with `G * secret_share`.

### Finding Description
- `ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation mode, `secret_share`, and then `n` group elements via `<C as Ciphersuite>::read_G(reader)` (`crypto/dkg/src/lib.rs:620-623`), which performs canonical-encoding checks only and accepts the identity point.
- `ThresholdKeys::new` validates only the *count* of shares and that participant indexes `<= n` (`crypto/dkg/src/lib.rs:355-365`), then computes `group_key` as `t.iter().map(|i| verification_shares[i] * interpolation_factor(*i, &t)).sum()` (`crypto/dkg/src/lib.rs:376-378`). Nothing checks the shares or the sum for identity.
- `group_key()` then returns `(core.group_key * scalar) + (G * offset)` (`crypto/dkg/src/lib.rs:445-447`) and is used by FROST `complete` via `self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum)` (`crypto/frost/src/sign.rs:465`) and is exposed to integrators (e.g., as a Bitcoin/Taproot output key).

### Impact Explanation
An unprivileged party who can cause a node/coordinator to deserialize attacker-crafted bytes through `ThresholdKeys::read` fully chooses the resulting group key:
- Set `verification_shares[1] = G * (k / λ_1)` for an attacker-known `k` and all other shares to identity → `group_key = G * k`. The attacker can then unilaterally produce valid Schnorr/BIP-340 signatures for the "threshold" key (`s = r + c·k`), i.e., a forged signature the verifier formula accepts — a concrete forgery, not just a wrong intermediate value.
- Set all shares to identity → `group_key = identity`, and any `(R, s)` with `s·G == R` verifies under `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs:88-110`).
- Similarly, per-participant `verification_shares[l]` being identity means the batch-verified share equation in `complete` accepts a crafted `responses[l]` consistent with the attacker's chosen key.

This mirrors the report exactly: the accumulator (`group_key` sum) silently accepts zero/attacker-chosen components, and downstream consumers trust the aggregate as authoritative.

### Likelihood Explanation
Reachability is limited to contexts feeding untrusted serialized `ThresholdKeys` into `read` (key recovery / restore paths, or any deployment accepting externally supplied key material). Where that surface exists, the attack is deterministic — no probability or cooperation of other signers is needed. Where only locally generated keys are used, this degrades to a hardening gap; hence Medium rather than High.

### Recommendation
- In `ThresholdKeys::new`, reject identity verification shares and reject an identity `group_key`.
- Verify consistency: `C::generator() * secret_share == verification_shares[&params.i()]`, so the sum cannot be decoupled from the deserialized secret share.
- Alternatively use an identity-rejecting `read_G` for verification shares in `ThresholdKeys::read`.

### Proof of Concept
```rust
// For any C: Ciphersuite; attacker-controlled `buf` fed to ThresholdKeys::<C>::read.
// Craft bytes: valid C::ID, t = n = 2, i = 1, Interpolation::Lagrange,
// secret_share = 1 (or anything).
// verification_shares[1] = (G * k) * λ_1^{-1}  for known k,
// verification_shares[2] = identity.
//
// group_key = shares[1]*λ_1 + shares[2]*λ_2 = G*k + 0 = G*k.
//
// Then for the Schnorr/FROST `verify(group_key, Rs, sum)` path:
//   pick any r, R = G*r, c = transcript challenge, s = r + c*k
//   => s*G == R + c*group_key  passes — forged signature under attacker-chosen key.
// With all-identity shares, group_key = identity and (R, s=r) verifies trivially.
```
Supporting code: `crypto/dkg/src/lib.rs:574-632` (read path accepting shares), `crypto/dkg/src/lib.rs:376-378` (unchecked sum), `crypto/frost/src/sign.rs:465` (verification against the derived key).