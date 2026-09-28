### Title
`ThresholdKeys::read` deserializes fields individually but never checks the secret share matches its verification share — ([File: crypto/dkg/src/lib.rs](https://github.com/blackvul/serai--022/blob/main/crypto/dkg/src/lib.rs#L574))

### Summary
Analogous to `latestRoundData()` returning a tuple where the caller checks `updatedAt` but not `answeredInRound`, `ThresholdKeys::read` validates each serialized field's *encoding* (curve ID, participant index, canonical scalars, canonical points) yet performs no check on the *semantic relationship* between the fields: the deserialized `secret_share` is never verified against `verification_shares[i]`, and the `verification_shares` map is never verified to form a consistent polynomial. `ThresholdKeys::new` (called at the end of `read`) only checks counts and index bounds — not that `generator() * secret_share == verification_shares[i]`.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574–632) reads `t`, `n`, `i`, the interpolation coefficients, `secret_share`, and `n` verification shares from an untrusted reader, then calls `ThresholdKeys::new` (lib.rs:349–391). `ThresholdKeys::new` checks:
- `verification_shares.len() == n` and all participant indexes `<= n` (lib.rs:355–365),
- interpolation applicability (lib.rs:367–374),

and then derives `group_key` as the interpolation of `verification_shares[1..=t]` (lib.rs:376–378). It **never** checks:
- `C::generator() * secret_share == verification_shares[i]` — the secret share may be completely unrelated to its claimed public share,
- that all `verification_shares` lie on a single degree-`t-1` polynomial — entries for indexes `> t` don't even affect `group_key`, so a corrupted share for a high-index participant is silently accepted.

The signing layer explicitly acknowledges this gap: `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:491–494) returns `FrostError::InternalError("everyone had a valid share yet the signature was still invalid")` with the comment *"The only known way to cause this, for valid parameters/algorithms, is to deserialize a semantically invalid FrostKeys."* — i.e., the downstream code knows `read` can produce internally inconsistent keys but cannot detect it until after a signing session is attempted.

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` (e.g., tampered key-share storage, a malicious backup/migration blob, or any transport carrying serialized `ThresholdKeys`) causes the victim to load keys that pass all deserialization checks but are semantically corrupt. Concretely:

- If `secret_share` doesn't match `verification_shares[i]`, every signature share the node produces is wrong. `complete()` will fail signature validation, then blame all participants (including honest ones) since `verify_share` checks each share against its verification share — the honest participants' shares are correct, the victim's is not, yielding `InvalidShare(victim)` or `InternalError`. The multisig is persistently unable to sign — funds held by the group key become unmovable (a liveness/funds-frozen condition, not merely a transient error).
- Worse, verification shares for indexes `> t` can be swapped arbitrarily without changing `group_key`, so a node can hold keys that report the correct `group_key()` while being incapable of ever producing a valid signature with certain signing sets.

This mirrors the oracle finding's shape: per-field validity is checked while cross-field consistency (the analog of `answeredInRound >= roundID`) is not, and the defect surfaces only later as "stale"/unusable data.

### Likelihood Explanation
Reachability is confirmed — `ThresholdKeys::read` is a public deserialization API over attacker-influenceable bytes, and the code itself documents that this is the known trigger for the `InternalError` path in `complete()`. Exploitation requires an attacker to influence the serialized key blob consumed by a participant (compromised storage, malicious restore payload, or a transport channel feeding `read`). It requires no collusion and no cryptographic break. Severity is bounded to liveness/funds-frozen rather than key recovery or forged signatures, consistent with Medium.

### Recommendation
In `ThresholdKeys::new` (or immediately at the end of `ThresholdKeys::read`), validate semantic consistency:
1. Check `C::generator() * secret_share == verification_shares[&params.i()]`.
2. Optionally verify the verification shares are consistent with a single polynomial (e.g., confirm the `group_key` interpolation computed from `1..=t` equals the interpolation obtained from other `t`-subsets, or require callers to supply a digest of the full share vector).
3. Consider making `read` reject inputs that would later trigger the documented `InternalError` path, converting a late runtime failure into an early deserialization error.

### Proof of Concept
```rust
// Conceptual PoC for C = any Ciphersuite, e.g. Ristretto
let legit: ThresholdKeys<C> = /* keys from a real PedPoP run */;
let mut buf = legit.serialize().to_vec();

// Corrupt the secret_share field (after id_len || id || t || n || i || interpolation tag).
// secret_share is located right after the interpolation encoding.
// Overwrite it with a random scalar's repr:
let offset = /* computed offset of secret_share in buf */;
buf[offset..offset + 32].copy_from_slice(random_scalar.to_repr().as_ref());

// read() succeeds: all fields are individually well-formed
let corrupted = ThresholdKeys::<C>::read::<&[u8]>(&mut buf.as_ref()).unwrap();

// group_key() still reports the original key (derived from verification_shares[1..=t])
assert_eq!(corrupted.group_key(), legit.group_key());

// But signing with corrupted.view(included) produces shares whose
// s_i * G != R_i + c_i * Y_i, so AlgorithmSignatureMachine::complete returns
// FrostError::InvalidShare(i) / InternalError("everyone had a valid share yet
// the signature was still invalid") — the exact path the code comments
// attributes to "deserializing a semantically invalid FrostKeys".
```

Note: I could not fully verify whether any in-scope caller re-validates `ThresholdKeys` after `read` (e.g., by re-deriving the share polynomial); if a caller does so, the practical impact narrows to that caller's behavior.