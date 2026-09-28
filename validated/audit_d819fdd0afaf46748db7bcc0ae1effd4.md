### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept zero secret shares and identity/attacker-controlled verification shares without validation, yielding an attacker-defined `group_key` - (File: crypto/dkg/src/lib.rs)

### Summary
The reported bug class is "missing validation of returned/deserialized data": values that should be sanity-checked (zero answer, stale round) are consumed as authoritative. The analog in Serai is `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) and `ThresholdKeys::new` (lib.rs:349-391): they read `secret_share` via `C::read_F` and all `verification_shares` via `<C as Ciphersuite>::read_G`, then compute `group_key` purely by interpolating the supplied shares — with no check that `secret_share != 0`, no check that any verification share is non-identity, and no check that `secret_share * G == verification_shares[i]`.

### Finding Description
- `ThresholdKeys::read` reads `n` verification shares with `<C as Ciphersuite>::read_G` (lib.rs:620-623). Unlike `Curve::read_G` in crypto/frost/src/curve/mod.rs:125-131, which rejects the identity point, `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) accepts any canonical point including identity.
- `secret_share` is read via `C::read_F` (lib.rs:618), which accepts `0`.
- `ThresholdKeys::new` only checks the count of shares, participant index bounds, and interpolation applicability (lib.rs:355-374). It never validates that shares are non-identity, that the secret share is non-zero, or that `G * secret_share` matches `verification_shares[params.i()]`.
- `group_key` is then derived as `sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` (lib.rs:376-378) — entirely determined by attacker-controlled bytes.

Because `write`/`serialize` deliberately round-trip only params, interpolation, `secret_share`, and `verification_shares` (lib.rs:538-562), a maliciously crafted serialization is fully accepted: the deserialized `ThresholdKeys` reports a `group_key()` the attacker chose (including one with known discrete logarithm, or the identity point), and a `secret_share`/`view` the attacker defined.

### Impact Explanation
Any component that feeds untrusted bytes to `ThresholdKeys::read` and then trusts `keys.group_key()` accepts an attacker-controlled group key. Downstream consumers (e.g., bitcoin-serai `Scanner::new(key)` / `tweak_keys` in networks/bitcoin/src/wallet/mod.rs:162-166, processor/src/networks/bitcoin.rs:650-654) derive deposit addresses directly from `group_key`. Funds sent to an address derived from an attacker-chosen group key are spendable by the attacker (the discrete log is known to them), so outputs are "reported received" yet belong to the adversary — directly paralleling a zero/stale price being treated as a real answer. The code itself acknowledges this class of failure only as a post-hoc `FrostError::InternalError` ("deserializ[ed] a semantically invalid FrostKeys", crypto/frost/src/sign.rs:492-494), which is detection after shares have already been produced rather than input validation.

### Likelihood Explanation
The read path is explicitly designed to ingest serialized key material; wherever that material can be supplied or substituted by an untrusted party (key delivery, backups, cross-component transport), a single crafted buffer controls the resulting group key. No cryptographic barrier exists — all n shares are attacker-chosen points. Exploitation requires a reachable `ThresholdKeys::read` on adversary-controlled bytes, which the in-scope rules assume, but it does not require breaking any primitive or colluding signers.

### Recommendation
In `ThresholdKeys::new` (or `read`), reject:
- `secret_share == 0`,
- any identity verification share (use the `Curve::read_G`-style identity rejection, or check `share.is_identity()` in `ThresholdKeys::new`),
- inconsistency: require `C::generator() * secret_share == verification_shares[params.i()]`,
- and optionally reject a resulting `group_key` that is identity.

### Proof of Concept
```rust
// Construct bytes that ThresholdKeys::<Secp256k1>::read accepts,
// producing a group_key whose discrete log the attacker knows (or identity).
let mut buf = vec![];
// C::ID header
buf.extend((Secp256k1::ID.len() as u32).to_le_bytes());
buf.extend(Secp256k1::ID);
// t = 1, n = 1, i = 1
buf.extend(1u16.to_le_bytes()); buf.extend(1u16.to_le_bytes()); buf.extend(1u16.to_le_bytes());
// interpolation: Lagrange
buf.push(1);
// secret_share = 0 (or attacker-chosen scalar a)
buf.extend(Secp256k1::F::ZERO.to_repr());
// verification_shares[1] = identity (or G * a)
buf.extend(Secp256k1::G::identity().to_bytes());

let keys = ThresholdKeys::<Secp256k1>::read(&mut &buf[..]).unwrap();
// group_key == identity; attacker knows dlog of any chosen group key.
// keys.view(&[Participant::new(1).unwrap()]) succeeds; sign produces shares
// consistent with the attacker-defined key; downstream scanners/addresses
// derived from keys.group_key() attribute attacker-owned funds as received.
```

Note: I could not verify whether a production component actually pipes network-supplied bytes into `ThresholdKeys::read` — the in-scope rules designate `ThresholdKeys::read` as an untrusted-bytes sink, and the finding stands on the missing validation itself, which is reachable whenever that sink is exercised with adversarial data.