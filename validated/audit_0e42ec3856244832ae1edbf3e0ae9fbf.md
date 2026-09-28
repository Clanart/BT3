### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept decodable but semantically invalid key material (identity verification shares, mismatched secret share) instead of validating the key relationship — ([File: crypto/dkg/src/lib.rs])

### Summary
The external report describes a `data_is_empty()` existence check being used in place of a semantic validation (correct mint / account type), so an attacker can substitute a "present but wrong" object and cause a downstream DoS. The same class exists in `crypto/dkg`: `ThresholdKeys::read` only checks that each field *decodes* (canonical scalar/point, plausible `t`/`n`/`i`), and `ThresholdKeys::new` only checks counts, thresholds, and indices — it never verifies that `secret_share * G == verification_shares[i]`, that verification shares are non-identity, or that the shares belong to a consistent polynomial. A structurally valid but semantically invalid key set is accepted and propagates into FROST signing.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation tag (with `Constant` reading `n` scalars), one `secret_share` scalar, and `n` verification shares via `C::read_G`, then hands them to `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:574-632`). `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) only verifies:

- `verification_shares.len() == n` and all indices `<= n`,
- `Constant` interpolation requires `t == n`,

then derives `group_key` from `verification_shares[1..=t]` (`crypto/dkg/src/lib.rs:376-378`). It never performs the semantic checks:

- `C::read_G` is the raw `Ciphersuite::read_G`, which accepts the identity point (unlike `Curve::read_G`, which explicitly rejects identity in `crypto/frost/src/curve/mod.rs:125-131`). Verification shares may be the identity.
- `read_F` accepts a zero `secret_share`, and there is no check that `C::generator() * secret_share == verification_shares[i]`.
- There is no check that the verification shares lie on a consistent polynomial at all — they are `n` arbitrary points.

This is the direct analog: `data_is_empty()` ↔ "the bytes decode into the right shape"; "verify the mint" ↔ "verify the scalar/point relationships that define valid key material." An attacker who supplies crafted serialized key bytes (a listed untrusted sink: `ThresholdKeys::read`) produces a `ThresholdKeys` that looks valid — non-empty, canonical, well-formed — but encodes a different "mint": a group key and share set unrelated to the real multisig.

Consequences downstream:

- `view()` (`crypto/dkg/src/lib.rs:463-533`) happily interpolates the bogus shares. If `included[0]` is the holder, the offset is added to a garbage share.
- In FROST `complete` (`crypto/frost/src/sign.rs:447-495`), individual share verification passes for any share consistent with its (attacker-chosen) verification share, yet the aggregate fails against `group_key`, reaching the explicitly acknowledged `InternalError("everyone had a valid share yet the signature was still invalid")` (`crypto/frost/src/sign.rs:493-494`) — a permanent DoS for that key set, exactly the report's impact class.
- Worse, an attacker can choose *all* `n` verification shares as multiples of a known discrete log (e.g., `y_j·G` with known `y_j`), making `group_key` a key whose discrete log they know for `t = n`/`Constant` setups — any signature produced under it validates for a key the attacker controls, i.e., the deserialized "keys" silently bind to attacker-chosen key material rather than erroring.

### Impact Explanation
Funds/signing bound to attacker-substituted key material: either a hard DoS at `complete()` (every honest share verifies, aggregate fails, `InternalError`) or signatures that verify under a group key whose discrete log the attacker selected. Analogous to a "present but wrong-mint token account" passing the existence check and breaking auction execution — here, "decodable but wrong-relation key set" passes deserialization and breaks/hijacks signing.

### Likelihood Explanation
Requires an unprivileged party to feed bytes to `ThresholdKeys::read` (an explicitly in-scope untrusted-input sink — e.g., key restoration, migration, or any path where serialized keys cross a trust boundary). No brute force needed; crafting such bytes is trivial since no consistency check exists. Rate: Medium.

### Recommendation
In `ThresholdKeys::new` (or at the end of `ThresholdKeys::read`), validate semantics, not just shape:

- Reject identity verification shares (use `Curve::read_G`-style identity rejection, or check `is_identity()` per share).
- Verify `C::generator() * secret_share == verification_shares[params.i()]` when `secret_share` is non-zero.
- Optionally require `group_key != identity`.

### Proof of Concept
```rust
// Crafted bytes for ThresholdKeys::<C>::read:
//   C::ID (correct curve), t = n = i = 1, interpolation = Lagrange,
//   secret_share = 0x00..00 (zero scalar — accepted by read_F),
//   verification_shares[1] = identity encoding (accepted by C::read_G).
// ThresholdKeys::read succeeds; group_key = identity.
// sign() then produces a share that verifies against the identity
// verification share, while complete() either returns InternalError
// (DoS) or — if all shares were chosen with known discrete logs —
// a signature valid under an attacker-known group key.
```

Confidence caveat: the exploit path presumes a reachable caller feeding adversary-controlled bytes to `ThresholdKeys::read` (per the stated scope of untrusted sinks); I did not trace a specific production caller within the in-scope crates, so the practical severity hinges on that plumbing.