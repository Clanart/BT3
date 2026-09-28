### Title
`ThresholdKeys` accepts a `secret_share` / `verification_shares` pair without validating consistency, allowing attacker-supplied key bytes to substitute the group key - (File: crypto/dkg/src/lib.rs)

### Summary
In the GMX report, a `stablePrice` was blindly accepted as the bid/ask without being validated against the oracle reference price. The analog in Serai is `ThresholdKeys::new`/`ThresholdKeys::read`: the `secret_share` and the per-participant `verification_shares` are taken directly from caller-supplied (deserializable) bytes, and the group key is derived solely from `verification_shares` (interpolated over participants `1..=t`). Nowhere is the consistency check `C::generator() * secret_share == verification_shares[i]` performed — the "trusted" secret share is accepted without validation against the values that actually define the key.

### Finding Description
`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) checks only the count and range of `verification_shares`, then computes `group_key` by Lagrange-interpolating `verification_shares[1..=t]`:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

The `secret_share` argument is stored verbatim and never checked against `verification_shares[params.i()]`. `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) funnels untrusted bytes (`C::read_F`, `C::read_G`) into `ThresholdKeys::new`, so a byte stream fully determines both the secret share and the group key with no mutual-consistency requirement.

Downstream, `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:447-495) sums shares and verifies the aggregate against `self.view.group_key()`. A share produced from an inconsistent `secret_share` simply fails `verify` and falls through to per-share `verify_share` blame — but if *all* fields are attacker-crafted to be internally consistent under an attacker-known secret `x` (i.e., `verification_shares[i] = x_i * G`, `secret_share = x_i`), signing completes successfully and yields a valid Schnorr signature under `x*G`, a key the attacker alone controls. The code acknowledges the gap: "The only known way to cause this... is to deserialize a semantically invalid FrostKeys" (sign.rs:492-494).

### Impact Explanation
Any consumer that loads `ThresholdKeys` from untrusted bytes (network message, backup file, coordinator-provided blob) and then signs produces valid Schnorr signatures under a group key whose discrete log is known to the attacker, while believing it operates the real threshold key. For bitcoin-serai flows this can manifest as signatures/funds attributed to an attacker-controlled key rather than the validator set's key — an unintended key substitution. In the mismatched case (inconsistent share vs. verification shares), honest signers produce invalid signatures and incorrectly reach `InternalError`/blame paths, giving a protocol-level integrity failure rather than a clean deserialization error.

### Likelihood Explanation
Exploitation requires the attacker to supply the serialized `ThresholdKeys` (e.g., a malicious coordinator or persisted-state path feeding `ThresholdKeys::read`). Where keys are produced locally via a real DKG (PedPoP verifies every share against commitments in `calculate_share`), the inconsistent state is unreachable. Reachability is therefore deployment-dependent, matching Medium severity — the same class as GMX's finding, which required a de-peg (external condition) plus the missing validation.

### Recommendation
In `ThresholdKeys::new`, validate the supplied share against its verification share: `C::generator() * *secret_share == verification_shares[&params.i()]`, returning a new `DkgError` variant on mismatch. Optionally also verify `group_key`'s interpolation is self-consistent. This mirrors GMX's recommendation to apply the same `validateRefPrice` check to the "trusted" input as to oracle prices.

### Proof of Concept
```rust
// Attacker-known secret
let x = <Secp256k1 as Ciphersuite>::F::random(&mut OsRng);
// t = n = 1 so a single consistent share suffices
let params = ThresholdParams::new(1, 1, Participant::new(1).unwrap()).unwrap();
let mut vs = HashMap::new();
vs.insert(Participant::new(1).unwrap(), Secp256k1::generator() * x);
// ThresholdKeys::new never checks secret_share vs. verification_shares;
// serialize these bytes and feed them to the victim's ThresholdKeys::read
let keys = ThresholdKeys::<Secp256k1>::new(
  params, Interpolation::Lagrange, Zeroizing::new(x), vs,
).unwrap();
// keys.group_key() == x*G; any FROST signature completed with these keys is
// valid under an attacker-controlled key, with no error raised.
```