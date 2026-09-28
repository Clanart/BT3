### Title
`ThresholdKeys::read` accepts a `secret_share` inconsistent with the deserialized `verification_shares`, yielding structurally valid but semantically corrupt threshold keys - (File: crypto/dkg/src/lib.rs)

### Summary
The bug class in the Chamilo report is *accepting attacker-controlled bytes after validating only a declared property (MIME type) rather than the actual content*. The Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new`: deserialization validates the curve ID, parameter bounds, canonicality of scalars/points, and share-count consistency — but never verifies that the deserialized `secret_share` actually corresponds to `verification_shares[params.i()]`. A corrupted/maliciously crafted serialized blob is accepted as a valid `ThresholdKeys`, and the inconsistency is only detected indirectly (or misattributed) at signing time.

### Finding Description
`ThresholdKeys::read` reads `t, n, i`, the interpolation mode, one `secret_share` scalar, and `n` verification shares, then hands them to `ThresholdKeys::new`.

`ThresholdKeys::new` checks:
- `verification_shares.len() == n` and all indices `<= n` (crypto/dkg/src/lib.rs:355-365),
- `Constant` interpolation only when `t == n` (367-374),
- then derives `group_key` solely from `verification_shares[1..=t]` via interpolation factors (376-378). [1](#0-0) 

At no point is the relation `C::generator() * secret_share == verification_shares[i]` (adjusted for interpolation) enforced. The downstream code is aware this gap exists — `AlgorithmSignatureMachine::complete` explicitly comments: *"The only known way to cause this, for valid parameters/algorithms, is to deserialize a semantically invalid FrostKeys"* (crypto/frost/src/sign.rs:491-494). [2](#0-1) 

By contrast, the DKG protocols themselves guarantee this invariant: `dealer::key_gen` computes `verification_shares[i] = G * secret_share_i` directly (crypto/dkg/dealer/src/lib.rs:51-53), and PedPoP's `calculate_share` batch-verifies `share_verification_statements` against the commitments before constructing keys (crypto/dkg/pedpop/src/lib.rs:487-491). Only the deserialization path skips the check.

### Impact Explanation
If serialized `ThresholdKeys` bytes cross a trust boundary (key backup/restore, key material relayed by a coordinator or peer, or any input path feeding `ThresholdKeys::read`), an unprivileged party can inject a blob where `secret_share` does not match `verification_shares[i]` while keeping a perfectly valid `group_key` and share map.

Consequences:
- The victim's node signs with the wrong share. The aggregate signature fails `algorithm.verify`, falling into the per-share blame pass (crypto/frost/src/sign.rs:465-489), where `verify_share` fails for the *victim's own* index — the honest participant is blamed as the faulty signer and can be ejected/slashed by honest-majority blame handling.
- This is a persistent liveliness/blame-poisoning condition: every signing attempt with the corrupt keys misidentifies the victim as malicious, while the tampered bytes look canonical and self-consistent.

### Likelihood Explanation
Exploitation requires an attacker to influence the bytes fed to `ThresholdKeys::read` — e.g., a distribution service, coordinator, or peer-supplied keystore — without knowing the real secret share. Setting `secret_share` to any value inconsistent with the published verification share suffices; no discrete-log work is needed since only `verification_shares` determine `group_key`. If key material is only ever loaded from fully trusted local storage, reachability is limited; wherever serialized keys transit an untrusted channel, the attack is trivial to mount. Severity: Medium (deterministic blame-misattribution / signer DoS on accepted corrupt keys).

### Recommendation
In `ThresholdKeys::new` (or `ThresholdKeys::read`), verify semantic consistency: check `C::generator() * interpolation_factor(i, {1..=t}) * secret_share == verification_shares[i]` — equivalently, that `G * secret_share` interpolated to index `i` equals `verification_shares[i]`. For `Interpolation::Lagrange` this reduces to `G * secret_share == verification_shares[i]` only when `i <= t`; a simpler robust check is to confirm the share is consistent via `view(&[1..=t])`/direct comparison `verification_shares[i] == G * secret_share` after accounting for the interpolation model used at share-generation time, and reject mismatched blobs with `DkgError`.

### Proof of Concept
```rust
// Deserialize a ThresholdKeys blob where secret_share is replaced by an
// unrelated scalar. The group key and verification shares remain a valid
// t-of-n sharing, so all structural checks pass.

let mut serialized = honest_keys.serialize().to_vec();

// Layout: id_len(4) || C::ID || t(2) || n(2) || i(2) || interp_tag(1)
//         || secret_share || verification_shares[n]
let share_offset = 4 + C::ID.len() + 2 + 2 + 2 + 1;

let forged = (C::F::random(&mut OsRng)).to_repr();
serialized[share_offset .. share_offset + forged.as_ref().len()]
    .copy_from_slice(forged.as_ref());

// Accepted: ThresholdKeys::new never checks G * share == verification_shares[i]
let evil_keys = ThresholdKeys::<C>::read(&mut serialized.as_slice()).unwrap();

// group_key unchanged -- derived only from verification_shares
assert_eq!(evil_keys.group_key(), honest_keys.group_key());

// In a signing session, evil_keys produces an invalid share; complete()
// falls through to verify_share, which flags params.i() (the victim) as
// the InvalidShare participant, and if all other shares are valid hits the
// InternalError arm documented at sign.rs:491-494.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L355-378)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
