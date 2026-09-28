### Title
Mass assignment in `ThresholdKeys::read` lets attacker-supplied bytes set privileged fields (`t`, `n`, `i`, `secret_share`, `verification_shares`) with no secret-share/verification-share consistency binding — ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The Flowise bug class is a deserialization/update path that merges client-controlled fields into server-controlled state without a whitelist or ownership check. The direct Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg/src/lib.rs`: every security-critical field of the threshold key — the parameters `t`, `n`, `i`, the interpolation table, the private `secret_share`, and the entire `verification_shares` map — is taken verbatim from the input byte stream, and the resulting `group_key` is recomputed from whatever `verification_shares` the bytes contained. There is no check that `secret_share` actually corresponds to `verification_shares[i]`, and no proof that the supplied shares were legitimately issued for this multisig.

### Finding Description
`ThresholdKeys::read` deserializes all fields of `ThresholdCore` directly from the reader (`crypto/dkg/src/lib.rs:574-632`):

```rust
// crypto/dkg/src/lib.rs:591-631
let (t, n, i) = ( read_u16()?, read_u16()?, Participant::new(read_u16()?) ... );
let interpolation = match interpolation[0] { 0 => Constant(...n scalars...), 1 => Lagrange, ... };
let secret_share = Zeroizing::new(C::read_F(reader)?);
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
ThresholdKeys::new(ThresholdParams::new(t, n, i)?, interpolation, secret_share, verification_shares)
```

`ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) only enforces:
- `verification_shares.len() == n`,
- all share indexes `<= n`,
- `Constant` interpolation only when `t == n`,

and then derives `group_key` as the interpolated sum over shares `1..=t` (`crypto/dkg/src/lib.rs:376-378`). It never verifies `C::generator() * secret_share == verification_shares[i]`, i.e., the deserialized private share is not bound to the claimed participant index or to the public share set. This is the analog of accepting `workspaceId`, `deployed`, `isPublic` from the client: the "server-controlled" consistency relation between a participant's secret share, their index `i`, and the published verification shares is neither enforced nor authenticated — the wire bytes can reassign it arbitrarily.

An attacker who can deliver a serialized `ThresholdKeys` blob to a victim (key handoff, reshare/import, or any path feeding untrusted bytes to `ThresholdKeys::read`, which is a listed untrusted-input sink) can therefore:
- Set `t = 1` and choose `verification_shares` for points whose discrete logs they know, producing a `group_key` they fully control while the victim holds a useless `secret_share`.
- Assign themselves/victim an arbitrary `i` and pair it with an unrelated `secret_share` — cross-participant share reassignment, the exact analog of cross-workspace chatflow reassignment.
- Select `Interpolation::Constant` with attacker-chosen coefficients, altering interpolation factors used by `view()` (`crypto/dkg/src/lib.rs:494-521`).

### Impact Explanation
High. If a validator imports or receives such bytes, the resulting `ThresholdKeys` advertises an attacker-controlled `group_key`. Any external funds addressed to that group key (e.g., via the Bitcoin scanner matching `script_pubkey` derived from the group key) are funds the validator believes are held by its multisig but are spendable only by the attacker — "funds reported received that are not spendable," plus silent theft. Alternatively a mismatched `secret_share`/`verification_shares` pair causes the honest node to emit signature shares that fail `verify_share`, which `complete` (`crypto/frost/src/sign.rs:491-494`) notes can only arise from "a semantically invalid FrostKeys" — confirming deserialization of inconsistent keys is an acknowledged failure mode with no defensive check at the read boundary.

### Likelihood Explanation
Medium. Exploitation requires an attacker to get their serialized bytes into a `ThresholdKeys::read` call on a victim — e.g., a relayed/imported key blob — rather than the DB-generated path where bytes were locally produced. Within the permitted threat model (untrusted bytes reaching `ThresholdKeys::read`), the attack needs no collusion, no leaked key, and no broken-BFT assumption; it is purely a missing validation at a deserialization boundary, exactly as in the Flowise report.

### Recommendation
- In `ThresholdKeys::new` (so `read` inherits it), assert `C::generator() * secret_share == verification_shares[&params.i()]`, rejecting blobs where the private share does not match the claimed participant's public share.
- Treat `ThresholdKeys` bytes as untrusted only when wrapped in an authenticated container (e.g., signed/encrypted PedPoP output); never `read` bare serialized keys from the network.
- For `Interpolation::Constant`, additionally verify the coefficients interpolate a consistent polynomial over the provided shares, or restrict `Constant` blobs to locally-generated data.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read where C::ID matches.
// t = 1, n = 2, i = victim's index (e.g., 2), Interpolation::Lagrange.
let mut blob = vec![];
blob.extend((C::ID.len() as u32).to_le_bytes());
blob.extend(C::ID);
blob.extend(1u16.to_le_bytes());                 // t = 1  (attacker-chosen threshold)
blob.extend(2u16.to_le_bytes());                 // n = 2
blob.extend(2u16.to_le_bytes());                 // i = 2 -> victim index
blob.push(1);                                    // Lagrange
blob.extend((C::generator() * 0u64 /* any scalar as "secret_share" */
    .to_bytes()).as_ref().to_vec());             // write a bogus secret_share repr
// verification_shares: points whose discrete logs the attacker knows
let a = <C as Ciphersuite>::F::random(&mut OsRng); // known to attacker
let b = <C as Ciphersuite>::F::random(&mut OsRng);
blob.extend((C::generator() * a).to_bytes().as_ref());
blob.extend((C::generator() * b).to_bytes().as_ref());

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// group_key is now interpolated over attacker-known shares; with t=1 the
// group key is a scalar multiple of a share the attacker knows the discrete
// log of => attacker controls the group key the victim believes is its
// multisig key. No error is raised because secret_share consistency with
// verification_shares[i] is never checked (crypto/dkg/src/lib.rs:349-391, 574-632).
```
Any funds sent to `keys.group_key()` (e.g., a Bitcoin deposit matched by the scanner) are unspendable by the victim and recoverable by the attacker.

Note: I was unable to inspect whether `crypto/dkg` contains additional modules beyond `lib.rs` (e.g., the PedPoP/encryption layer) in this index — `crypto/dkg/src` lists only `lib.rs` — so I could not confirm whether a production wire path delivers attacker bytes to `ThresholdKeys::read`; the finding stands on the deserialization boundary itself per the stated reachability rules.