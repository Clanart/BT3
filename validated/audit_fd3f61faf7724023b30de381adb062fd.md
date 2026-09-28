### Title
`ThresholdKeys::read` accepts attacker-crafted key blobs whose reported `group_key` is decoupled from the holder's actual share — obscuring which key was imported - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::new` computes `group_key` by interpolating only the verification shares of participants `1..=t`, and never checks that the local `secret_share` corresponds to `verification_shares[i]` or that shares `t+1..=n` are consistent with the group key. `ThresholdKeys::read` deserializes arbitrary untrusted bytes straight into this structure. An attacker who supplies a serialized `ThresholdKeys` blob can therefore make it report any group key while encoding a secret share that does not belong to that key — the exact "import hides/obscures which key was imported" class of CVE-2019-9150.

### Finding Description
In `ThresholdKeys::new`, the group key is derived as the interpolation of `verification_shares[1..=t]` only:

`crypto/dkg/src/lib.rs:376-378`
```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

No consistency checks follow:
- It is never verified that `C::generator() * secret_share == verification_shares[&params.i()]` (compare `secret_share` stored unconditionally at `crypto/dkg/src/lib.rs:380-391`).
- Verification shares for participants `t+1..=n` are stored and later used for share verification (`view()`, `crypto/dkg/src/lib.rs:500-507`) but contribute nothing to `group_key`, so two blobs with identical first-`t` shares report the same group key while encoding entirely different signing sets.

`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, `i`, the interpolation variant, the secret share, and all `n` verification shares purely from attacker-controlled bytes, then calls `ThresholdKeys::new`. Nothing binds the blob to any externally authenticated group key.

### Impact Explanation
An attacker who can feed serialized `ThresholdKeys` bytes to a party (e.g., during key recovery, migration, or rehydration from an untrusted store — `ThresholdKeys::read` is an explicitly in-scope deserialization surface) can:

1. Craft a blob whose `group_key()` equals the victim's *expected* multisig key (e.g., a deposit address) by choosing `verification_shares[1..=t]` to interpolate to it, while setting the victim's `i > t` and `secret_share`/`verification_shares[i]` to attacker-known values. The holder sees the "correct" group key — the obscured import — yet their share is not a share of that key.
2. Any signature the holder produces fails aggregate `verify()` (`crypto/frost/src/algorithm.rs:214-217`), and in PedPoP/FROST blame flows the holder can be made to appear faulty. Funds sent to the reported group key are unspendable by this holder: deposits reported received are not spendable.
3. Because shares `> t` are unbound, an attacker can also mint multiple blobs claiming the same group key with mutually inconsistent verification shares, causing share-verification failures that cannot be attributed correctly.

### Likelihood Explanation
Requires the attacker to control serialized key material consumed via `ThresholdKeys::read`. This is reachable wherever key blobs cross a trust boundary (backups, recovery shares exchanged between peers, DB entries written by a less-trusted component). The caller has no API to distinguish a genuine blob from a crafted one since `group_key()` itself is attacker-influenced — precisely the "no user/integrity confirmation of which key was imported" condition from CVE-2019-9150.

### Recommendation
In `ThresholdKeys::new` (and therefore `read`), enforce:
- `C::generator() * secret_share == verification_shares[&params.i()]` (reject mismatched shares), and
- group-key commitment over **all** `n` verification shares, or at minimum verify that the full `n`-share interpolation under `Interpolation::Lagrange`/`Constant` reproduces the same `group_key` as the `1..=t` subset, so every encoded share is bound to the reported key.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read targeting a victim
// expected to hold index i=2 of a 2-of-3 for group key G_v.

// 1. Choose verification_shares[1], [2] such that
//    lagrange_1({1,2})*V1 + lagrange_2({1,2})*V2 == G_v  (the expected key).
//    e.g. V1 = G_v - V2 for interpolation factor 1; any V2 works.

// 2. Set verification_shares[3] = G * a, secret_share = a for an
//    attacker-known scalar a (victim's i = 3 is not bound to G_v).

// 3. Serialize: ID || t=2 || n=3 || i=3 || Lagrange || a || V1 || V2 || G*a.

// Victim: let keys = ThresholdKeys::<C>::read(&mut blob).unwrap();
// assert keys.group_key() == G_v  // passes — the obscured import
// keys.view([1,2,3]) produces share λ_3 * a; final signature fails
// SchnorrSignature::verify under G_v and the victim is blamed.
```

Files: `crypto/dkg/src/lib.rs` (`ThresholdKeys::new` lines 349-391, `ThresholdKeys::read` lines 574-632, `view` lines 463-533); `crypto/frost/src/algorithm.rs` (`verify` lines 214-217).