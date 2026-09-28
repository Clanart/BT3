### Title
Crafted serialized `ThresholdKeys` accepted without share/key consistency check yields an attacker-chosen group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation method, the `secret_share`, and the full `verification_shares` map entirely from attacker-controlled bytes, then hands them to `ThresholdKeys::new`. `new` derives `group_key` as the interpolation over `verification_shares[1..=t]` — the attacker-supplied points — and never verifies that the provided `secret_share` is consistent with `verification_shares[i]` (i.e., that `G * secret_share == verification_shares[i]`). Analogous to CVE-2021-23991 (a crafted version of a key with an invalid subkey is imported and used), an unprivileged party feeding crafted bytes to `ThresholdKeys::read` installs a key whose public group key does not correspond to the local secret share — or any secret the victim holds.

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632), every field is read from the wire with only structural checks: `ThresholdParams::new` bounds, and per-point deserialization via `read_G`. The `secret_share` and all `verification_shares` are attacker-supplied. `ThresholdKeys::new` (lines 349-391) computes:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

The group key is therefore fully determined by bytes the attacker wrote; the deserialized `secret_share` plays no role in `group_key()`. There is no check anywhere — not in `read`, `new`, or `view` — that `C::generator() * secret_share == verification_shares[i]` or that the shares form a coherent sharing of the group key. `view()` (lines 463-533) interpolates `secret_share` and multiplies the stored `verification_shares` independently, so the inconsistency survives silently into signing.

### Impact Explanation
Two consequences, both reachable from untrusted bytes:

1. **Attacker-controlled group key.** The attacker sets `verification_shares` to points whose interpolating polynomial has a known constant term `s` (e.g., simply `G*s_j` for attacker-chosen scalars), so `group_key()` is a key the attacker alone knows the discrete log of. Any consumer that treats `group_key()` as the shared wallet key (deposit address, Bitcoin output key) will report/accept funds at an address spendable only by the attacker — funds reported received that are not spendable by the victim set.
2. **Permanent signing failure.** With `secret_share` inconsistent with `verification_shares[i]`, every FROST signature share the victim produces fails verification, so the group can never sign — the direct analog of Thunderbird failing to send to the crafted key.

Either way, the victim uses a crafted, internally inconsistent key exactly as in the CVE.

### Likelihood Explanation
Any code path deserializing `ThresholdKeys` from bytes that an unprivileged counterparty can influence (key-distribution messages, synced/cached state, backups supplied by a peer) exposes this. The attacker needs no secret knowledge and no valid share — only the ability to supply the serialized blob — so likelihood is moderate wherever deserialization of remote key material occurs.

### Recommendation
In `ThresholdKeys::new` (or `read`), verify consistency: require `C::generator() * secret_share == verification_shares[&params.i()]`, and ideally reject identity/non-canonical points among `verification_shares`. This forces the deserialized key to be a coherent sharing, so a crafted "invalid subkey" cannot silently produce a divergent `group_key`.

### Proof of Concept
```rust
// Attacker knows scalar s; wants victim to adopt group key G*s
// while victim's stored secret_share is arbitrary garbage.
let s = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
let evil_share = <Ristretto as Ciphersuite>::F::random(&mut OsRng);

// t = n = 2 for simplicity; verification_shares are points whose
// Lagrange interpolation at 0 equals G*s (e.g., G*s and G*2s).
let mut bytes = vec![];
// ... write C::ID, t=2, n=2, i=1, Lagrange tag=1 ...
// write evil_share as the secret share
// write verification_shares[1] = G*s, verification_shares[2] = G*(2s)
let keys = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice()).unwrap();

// group_key() == G*s: attacker-controlled, yet victim's
// original_secret_share() == evil_share, inconsistent with
// verification_shares[1]. Signing fails; deposits to group_key
// are spendable only by the attacker.
```

Root cause: `crypto/dkg/src/lib.rs` — `ThresholdKeys::read` (lines 574-632) and `ThresholdKeys::new` (lines 376-378) trust attacker-supplied `verification_shares` to define `group_key` without checking the deserialized `secret_share` is consistent with them.