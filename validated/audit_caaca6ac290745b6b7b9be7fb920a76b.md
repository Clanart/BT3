### Title
`ThresholdKeys::read` / `ThresholdKeys::new` trust the serialized secret share as already-consistent with the verification shares, skipping re-validation — (File: crypto/dkg/src/lib.rs)

### Summary
The ADOdb bug class is a "already-processed" short-circuit: `adodb_addslashes` returned attacker input verbatim when it merely *looked* quoted, letting a raw unescaped value flow into a security-sensitive context. The Serai analog is in DKG key deserialization. When threshold keys are reconstructed from untrusted bytes via `ThresholdKeys::read`, the secret share is accepted as-if — it is assumed to have already been validated against the verification shares during DKG completion — and no consistency check (`secret_share * G == verification_shares[i]`) is ever performed.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) parses `t`, `n`, `i`, the interpolation method, a `secret_share` scalar (line 618), and `n` verification shares (lines 620-623), then hands everything to `ThresholdKeys::new` (lines 625-631). `ThresholdKeys::new` (lines 349-391) validates only:

- the number of verification shares equals `n` (line 355),
- participant indices are `<= n` (line 362),
- constant interpolation is only used when `t == n` (line 369).

It then derives `group_key` purely from the *verification shares* (lines 376-378) and stores the attacker-supplied `secret_share` unmodified (line 384). At no point does it verify that `C::generator() * secret_share == verification_shares[i]` — the check performed during DKG completion is implicitly assumed to have "already happened," exactly like the removed `return $s; // already quoted` line.

`ThresholdView::view` compounds this: it interpolates the stored `secret_share` (lines 494-498) and computes `verification_shares` from the stored shares (lines 500-507), so a tampered share produces a view whose `secret_share()` provably does not match `verification_share(i)`.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (an allowed input surface per scope rules) supplies verification shares defining an arbitrary group key `Y` plus an unrelated secret share `x'`. The holder:

1. Adopts `group_key() = Y` and reports `verification_share(i)` derived from attacker-chosen points.
2. In FROST signing, produces `s_i = d_i + e_i·ρ_i·λ_i·x'` (crypto/frost `algorithm.rs`) where the nonce commitments are consistent but the share is not: `s_i·G ≠ R_i + c·λ_i·Y_i`, so every partial signature this node emits fails share verification and is correctly blamed as malicious.

Result: a node/controller loaded with crafted key material believes it participates honestly in the threshold group for `Y`, but every signature share it produces is invalid and gets it blamed/slashed. If the crafted material also drives the scanner (`ReceivedOutput` / offset derivation in networks/bitcoin/src/wallet/mod.rs), outputs for `Y` are reported received while this holder can never contribute a valid share — funds reported received that are not (from this node's share) spendable. There is no path to forging a valid signature or recovering other shares, so the impact is integrity-of-role/availability, not key compromise — Medium.

### Likelihood Explanation
Requires an untrusted party to control the bytes passed to `ThresholdKeys::read`. Within the stated scope (untrusted bytes to `ThresholdKeys::read` are reachable), the attack needs no collusion, no valid share, and no cryptographic break — only a malformed serialization, which is trivially constructed since the format has no integrity tag binding `secret_share` to `verification_shares`. The mitigating factor is that key material is normally loaded from local trusted storage rather than the network, capping severity at Medium.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), re-verify share consistency:

```rust
if C::generator() * secret_share.deref() != verification_shares[&params.i()] {
  Err(DkgError::InvalidSecretShare)?;
}
```

For `Interpolation::Constant`, additionally verify `secret_share == c[i-1]` (the `i`-th coefficient). For `Lagrange`, the share-to-verification-share equality check suffices. Alternatively, bind the share to the shares with a checksum/MAC over the serialization so tampered encodings are rejected before use.

### Proof of Concept
```rust
// Attacker crafts serialized ThresholdKeys for curve C:
//   t = 2, n = 3, i = 1, Interpolation::Lagrange
//   secret_share = random scalar x' (not corresponding to any share)
//   verification_shares = {1: Y1, 2: Y2, 3: Y3} with Y1 != x'*G
let keys = ThresholdKeys::<C>::read(&mut crafted_bytes).unwrap();
// Succeeds: no check that x' * G == Y1
let view = keys.view(vec![p1, p2]).unwrap();
// view.secret_share() = λ1 * x', but view.verification_share(p1) = λ1 * Y1
// => in frost::AlgorithmMachine::sign, the produced SignatureShare
//    s = d + e*ρ*λ*x' fails s*G == R + c*λ*Y1 -> invalid share -> blamed
```