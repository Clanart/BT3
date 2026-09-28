### Title
Tag-selected type confusion in `ThresholdKeys::read` allows a forged/inconsistent key set with an attacker-chosen group key - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` deserializes `(t, n, i)`, a 1-byte `interpolation` tag, and the remaining buffer, where the tag selects between two different interpretations of the byte stream (`Interpolation::Constant` consumes `n` scalars; `Interpolation::Lagrange` consumes none). The resulting `ThresholdKeys` is built via `ThresholdKeys::new`, which performs only structural validation — it never checks that `C::generator() * secret_share == verification_shares[i]`, that the verification shares form a consistent polynomial, nor does it reject identity verification shares (it uses `Ciphersuite::read_G`, not the identity-rejecting `Curve::read_G`). The `group_key` is then computed by interpolating whatever points the attacker supplied for participants `1..=t`.

### Finding Description
The bug-class analog of CVE-2019-6215 (type confusion: bytes interpreted as a different object shape than intended) maps onto the variant-tag deserialization in `ThresholdKeys::read`:

- `crypto/dkg/src/lib.rs:604-616` — a single attacker-controlled byte selects `Interpolation::Constant` (consume `n` `read_F` scalars) vs `Interpolation::Lagrange` (consume nothing). The same serialized buffer therefore parses into two semantically different `ThresholdKeys` objects depending on the tag — a canonical type-confusion shape.
- `crypto/dkg/src/lib.rs:349-390` — `ThresholdKeys::new` checks `verification_shares.len() == n`, participant indexes `<= n`, and `Constant ⇒ t == n`, but does not verify the secret share against `verification_shares[i]` and does not verify consistency of shares with the implied polynomial. [1](#0-0) 
- `crypto/dkg/src/lib.rs:620-623` — verification shares are read with `<C as Ciphersuite>::read_G`, which only enforces canonical encoding; unlike `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`) it does not reject the identity point. [2](#0-1) 
- `crypto/dkg/src/lib.rs:376-378` — `group_key` is derived purely from the attacker-supplied `verification_shares[1..=t]`, so the attacker fully controls the group key of the deserialized keys.

An unprivileged party that can supply bytes to `ThresholdKeys::read` (key import/recovery paths listed as reachable in scope) can therefore produce a `ThresholdKeys` whose `group_key()` is a key whose discrete log the attacker knows, while `params()`, `interpolation`, and `verification_shares` are all internally inconsistent with `secret_share`.

### Impact Explanation
If the crafted `ThresholdKeys` is adopted by a signer, two reachable outcomes exist, both noted as in-scope acceptance criteria:

1. The group key of the imported keys is attacker-controlled (a polynomial whose secret the attacker knows). Outputs/funds attributed to that group key are spendable by the attacker, not by the validator set — "funds reported received that are not spendable" by the intended group.
2. If honest verification shares for `1..=t` are retained (so `group_key` stays the real group key) but `verification_shares[j]` for some `j > t` is corrupted (e.g., set to identity, which `Ciphersuite::read_G` accepts), honest participant `j`'s valid signature share will fail `verify_share` during `complete`, causing `j` to be blamed/slashed — or a malicious share may verify against the corrupt share. The code itself acknowledges this semantic-invalidity path: `complete` ends with `InternalError("everyone had a valid share yet the signature was still invalid")`, which the comment attributes to deserializing a semantically invalid FrostKeys. [3](#0-2) 

### Likelihood Explanation
Medium. Exploitation requires an attacker-controlled buffer reaching `ThresholdKeys::read` rather than self-generated DKG output; such paths exist wherever serialized keys are imported, recovered, or relayed rather than produced in-process. The cryptographic checks that would catch this (secret-share ↔ verification-share binding) are entirely absent, so any attacker-reachable call succeeds deterministically. It is not High because a purely local key store not fed by untrusted input is unaffected.

### Recommendation
In `ThresholdKeys::read` / `ThresholdKeys::new`:

- Verify `C::generator() * secret_share == verification_shares[&params.i()]` after constructing params.
- Reject identity verification shares (use `Curve::read_G` semantics or an explicit `is_identity` check).
- For `Interpolation::Constant`, verify the coefficients are consistent with the verification shares (e.g., `verification_shares[l] == c[l-1] * G`-style binding per the Constant scheme), or at minimum reject non-canonical/trailing bytes.
- Reject trailing bytes in `read` so the two tag interpretations cannot alias the same buffer silently.

### Proof of Concept
```rust
// Attacker-crafted buffer for ThresholdKeys::<C>::read:
//   id_len || C::ID || t || n || i || tag=1 (Lagrange) || secret_share || n * points
// Set t = n (any), i = 1.
// Let a = attacker-known scalar. Set:
//   verification_shares[l] = G * f(l) for l in 1..=n, where f is an attacker-chosen
//   polynomial with f(0) = a  ->  group_key == G * a (attacker knows the key).
//   secret_share = arbitrary (e.g., 1), NOT f(1).
//
// ThresholdKeys::read succeeds: params valid, n points canonical.
// keys.group_key() == G * a  (attacker-controlled group key)
// keys.original_verification_share(1) != G * secret_share  (inconsistent, unchecked)
//
// Variant: keep verification_shares[1..=t] honest (real group key preserved),
// set verification_shares[j] = identity for some j > t (accepted by
// Ciphersuite::read_G). In a signing session including j, j's honest share fails
// verify_share -> FrostError::InvalidShare(j) -> j is blamed despite being honest.
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

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
