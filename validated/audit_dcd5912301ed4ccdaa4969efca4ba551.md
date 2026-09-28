### Title
Missing identity check on verification shares lets `ThresholdKeys::new`/`ThresholdKeys::read` build a `group_key` equal to the identity, yielding a publicly forgeable key - (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes an `initialize` function that validates one address (`_collateral`) against zero but forgets another (`_owner`), letting a critical parameter be set to the zero value. The Serai analog is in `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`): it validates the *count* and *indices* of the supplied verification shares, but never checks that the shares are non-identity points nor that the resulting `group_key` — the linear combination of the first `t` verification shares — is non-identity. This constructor is reachable purely from untrusted bytes via `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), an explicitly in-scope deserialization entry point.

### Finding Description
`ThresholdKeys::new` computes the group key as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(`crypto/dkg/src/lib.rs:376-378`)

The only input validation performed is `verification_shares.len() == n` and `participant <= n` (`crypto/dkg/src/lib.rs:355-365`). `C::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) enforces only canonical encoding, not non-identity. An attacker crafting serialized `ThresholdKeys` can therefore:

- Supply identity points directly as verification shares (where the group's `from_bytes` admits them), or — more robustly —
- Supply non-identity shares whose Lagrange-weighted sum is the identity. For `t = n = 2` with factors `λ1, λ2`, choosing `V2 = -(λ1/λ2) * V1` makes `group_key = λ1·V1 + λ2·V2 = identity`, using only ordinary non-identity points, so even a hypothetical identity rejection in `read_G` would not catch it.

No check anywhere in `new` or `read` rejects `group_key == identity`. The keys then flow into FROST signing/verification, where `SchnorrSignature::verify` checks `R + cA − sG == 0` via `batch_statements` (`crypto/schnorr/src/lib.rs:88-110`). With `A = identity`, the equation reduces to `R == sG`, so **anyone** can produce a valid signature for any message/challenge — the key has no secret.

The same missing-zero-check shape also exists at the share level: `GeneratorCommitments::read` (`crypto/frost/src/nonce.rs:34-36`) and `NonceCommitments::read` (`crypto/frost/src/nonce.rs:74-80`) accept identity nonce commitments fed via `read_preprocess` (`crypto/frost/src/sign.rs:276-281`), though that path alone is weaker since a malicious peer still cannot produce a matching share without the secret.

### Impact Explanation
A `ThresholdKeys` whose `group_key` is the identity corresponds to a group secret of `0`: every signature on it is trivially forgeable by an unprivileged party (`R = sG` for any chosen `s`). Concretely, any consumer that deserializes attacker-influenced `ThresholdKeys` (e.g., recovering/restoring key material from untrusted storage or transport through `ThresholdKeys::read`) ends up with a "threshold key" that provides zero security — signatures under it can be forged without any secret share, and in the Bitcoin wallet path funds sent to such a key are spendable by anyone (equivalently, not safely spendable by the owner). This matches the accepted impact classes: forged signatures and funds reported under a key that is not actually controlled.

### Likelihood Explanation
Exploitation requires an attacker to control the bytes deserialized into `ThresholdKeys` — a narrower reach than a network message, but `ThresholdKeys::read` is listed among the in-scope untrusted-input APIs, and the computation requires no secret knowledge: the Lagrange factors over `included = 1..=t` are fully public (`Interpolation::interpolation_factor`, used at `crypto/dkg/src/lib.rs:378`), so constructing shares that sum to identity is straightforward arithmetic. Consistency checks elsewhere don't help: `recover_key` (`crypto/dkg/recovery/src/lib.rs:80-82`) verifies `generator * recovered == group_key`, and a recovered secret of `0` satisfies this when `group_key` is identity, so even the integrity check passes.

### Recommendation
In `ThresholdKeys::new`, after computing `group_key`, reject `group_key.is_identity()` (and optionally reject identity verification shares in the validation loop at `crypto/dkg/src/lib.rs:361-365`). Additionally, `Commitments::read`/`GeneratorCommitments::read` in `crypto/frost/src/nonce.rs` should reject identity commitments per RFC 9591's nonce-commitment validity requirement.

### Proof of Concept
```rust
// Construct ThresholdKeys bytes whose group_key is the identity, for t = n = 2.
// Lagrange factors for included = {1, 2}: λ1 = 2/(2-1) = 2, λ2 = 1/(1-2) = -1.
// Choose V1 = G, V2 = -2*G  => group_key = 2*G + (-1)*(-2G)... pick V2 so λ1*V1 + λ2*V2 = 0.

// Serialize a ThresholdKeys<C> by hand:
//   id_len = C::ID.len(), id = C::ID
//   t = 2, n = 2, i = 1
//   interpolation byte = 1 (Lagrange)
//   secret_share = arbitrary F (never checked against shares)
//   verification_shares[1] = P (canonical non-identity)
//   verification_shares[2] = -(λ1/λ2) * P   // so weighted sum = identity
//
// ThresholdKeys::<C>::read(&mut bytes) -> Ok(keys)
// assert!(keys.group_key().is_identity());   // no rejection
//
// Forgery: for any message, pick s, set R = generator()*s.
// SchnorrSignature { R, s }.verify(keys.group_key(), challenge) computes
//   R + c*identity - sG = sG - sG = identity  -> true.
// A valid FROST/Schnorr signature under the "threshold" key is produced
// with zero knowledge of any secret share.
```