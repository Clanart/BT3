### Title
`ThresholdKeys::new` derives the group key only from participants `1..=t`, leaving all other verification shares unauthenticated — an "authentic" group key can wrap malicious shares - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to `VaultFactory` producing an authentic vault that wraps unverified `TwabController`/`PrizePool` sub-components, `ThresholdKeys::new` (and `ThresholdKeys::read`, which feeds untrusted bytes into it) produces a key set whose `group_key()` looks authentic — it is honestly derived and will match a real multisig key — while the verification shares for all participants with index `> t` are completely unauthenticated. The group key is computed solely from the shares of participants `1..=t`; every other participant's verification share is stored verbatim without ever being bound to the group key or checked for consistency.

### Finding Description
`ThresholdKeys::new` validates only the count of `verification_shares` (`== n`) and that each `Participant` index is `<= n`. It then computes:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(`crypto/dkg/src/lib.rs:376-378`)

Only shares `1..=t` contribute to `group_key`. Shares `t+1..=n` are stored in `core.verification_shares` without any check — they are not required to lie on the same polynomial, not checked to be non-identity/non-torsion, and `secret_share` is never checked against `verification_shares[params.i()]` either. `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reconstructs a `ThresholdKeys` purely from attacker-supplied bytes via `read_F`/`read_G` and calls `ThresholdKeys::new`, so this is reachable from untrusted serialized data.

Consequently, a serialized `ThresholdKeys` blob can present a `group_key()` identical to a legitimately generated t-of-n multisig (the "authentic vault") while the verification shares for participants `t+1..=n` are attacker-chosen arbitrary points (the "malicious TwabController/PrizePool"). During FROST signing, `ThresholdKeys::view` interpolates whichever `verification_shares[i]` are in `included` (`crypto/dkg/src/lib.rs:500-506`), and share validation during `complete` trusts these shares — so the corrupted components are only exercised, and only fail, at signing time, exactly as the malicious sub-contracts in the vault report only cause loss when interacted with.

### Impact Explanation
Because the group key is authentic-looking while the remaining shares are unauthenticated:

- A `ThresholdKeys` blob can advertise the group key of a real multisig while participant `j > t`'s verification share is attacker-controlled. When a signing set includes `j`, the participant's share is evaluated against the fake share. Setting the share to identity makes any `share * G == R_i` pass local share verification while producing a final signature that does not verify under the group key — or, equivalently, a blob whose shares cannot jointly sign at all. Funds addressed to the group key are reported received under an "authentic" key yet are not spendable by the declared signing set — matching the report's "users may lose funds while interacting with such vaults" (here: funds locked/DoS'd under an authentic-looking key).
- The secret share's consistency with `verification_shares[i]` is also never enforced, so a blob can carry a group-authentic key with a secret share that does not correspond to its claimed verification share.

This is a Medium-severity integrity flaw: the object's public identity (`group_key()`) provides false assurance about the authenticity of components it silently embeds, and the failure mode is loss of spendability of funds under the group key.

### Likelihood Explanation
Reachable whenever `ThresholdKeys`/`ThresholdView` are materialized from data influenced by an unprivileged party (`ThresholdKeys::read` is an accepted untrusted-input path) rather than produced locally by `musig()`/`PedPoP`. Any consumer that treats `group_key()` as proof that the whole share set is consistent — the analogue of trusting the `VaultFactory` mapping — is exposed. Exploitation requires the corrupted shares to enter a signing set, which is the normal operating path for indexes `> t` whenever the signing quorum doesn't happen to be exactly `1..=t`.

### Recommendation
In `ThresholdKeys::new`, authenticate the full component set instead of only `1..=t`:
- Verify `C::generator() * secret_share == verification_shares[params.i()]`.
- For `Interpolation::Lagrange`, require the verification shares to be consistent with a degree `t-1` polynomial (e.g., check that the shares of `t+1..=n` interpolate to the same group key computed from `1..=t`, or require commitments/Feldman-style coefficients rather than bare shares).
- Reject identity/torsion verification shares.

### Proof of Concept
```rust
// t-of-n where n > t
let t = 2u16; let n = 3u16;
// Honest shares for 1..=t reproduce the real group key
let honest: HashMap<Participant, RistrettoPoint> = /* shares from a real DKG */;

// Crafted blob: keep shares 1..=t authentic, replace share 3 with attacker point
let mut shares = honest.clone();
shares.insert(Participant::new(3).unwrap(), RistrettoPoint::generator() * x_evil);

let keys = ThresholdKeys::<Ristretto>::new(
  ThresholdParams::new(t, n, Participant::new(1).unwrap()).unwrap(),
  Interpolation::Lagrange,
  secret_share_1,
  shares,
).unwrap(); // succeeds — share 3 never checked

// group_key() equals the honest multisig key ("authentic" container)
assert_eq!(keys.original_group_key(), honest_group_key);

// A signing set including participant 3 interpolates the forged share
// (lib.rs:500-506); signatures fail or the set is unspendable under the
// otherwise-authentic group key.
```

The serialized equivalent is crafted via `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), which performs no consistency check beyond `ThresholdKeys::new` itself.