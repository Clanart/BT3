### Title
`ThresholdKeys::read` accepts attacker-controlled key material without binding `secret_share` to `verification_shares`, yielding a substituted group key - (File: crypto/dkg/src/lib.rs)

### Summary
The BitKeep incident is a package-substitution class bug: attacker-injected bytes replaced the legitimate wallet's key material, so users' funds flowed to attacker-controlled keys. The analogous sink in Serai is `ThresholdKeys::read`, which is explicitly an untrusted-bytes deserialization path. Neither `ThresholdKeys::read` nor `ThresholdKeys::new` verifies that the deserialized `secret_share` actually corresponds to `verification_shares[params.i]` (i.e., `C::generator() * secret_share == verification_shares[i]`), nor that the derived `group_key` is related to the local share at all. The group key is computed purely from the attacker-supplied verification shares for participants `1..=t`.

### Finding Description
`ThresholdKeys::read` fully trusts the serialized `(t, n, i, interpolation, secret_share, verification_shares)` tuple and hands it to `ThresholdKeys::new`. `ThresholdKeys::new` only checks:
- `verification_shares.len() == n` and indices `<= n` (`crypto/dkg/src/lib.rs:355-365`)
- `Constant` interpolation implies `t == n` (`crypto/dkg/src/lib.rs:367-374`)

It then derives `group_key` by interpolating `verification_shares[1..=t]` (`crypto/dkg/src/lib.rs:376-378`). Nowhere is the invariant `G * secret_share == verification_shares[i]` enforced, and `group_key()`/`view()` propagate this attacker-defined group key (`crypto/dkg/src/lib.rs:444-447`, `463-533`).

Because `PedPoP`'s `calculate_share` and `GeneratorPromotion::complete` both call `ThresholdKeys::new` only after cryptographically validating the shares (`crypto/dkg/pedpop/src/lib.rs:487-529`, `crypto/dkg/promote/src/lib.rs:146-166`), the invariant holds when keys come from the DKG — but not when they come from `ThresholdKeys::read` over attacker-influenced storage, backups, or recovery bytes. `recover_key` does check `G * res == group_key` (`crypto/dkg/recovery/src/lib.rs:80-82`), but nothing on the `ThresholdKeys::read` path does; the reader gets a `ThresholdKeys` whose `group_key()` is whatever the byte supplier chose.

An attacker who can substitute the serialized key material (the precise analog of the hijacked BitKeep APK) can set all `n` verification shares to a polynomial they control, making `group_key()` return an attacker-owned key while the victim's `secret_share` is unrelated. Any party/integrator that treats `group_key()` as "our threshold address" will accept deposits to an address no honest signer set can ever spend.

### Impact Explanation
This meets the "funds reported received that are not spendable" acceptance criterion: the deserialized `ThresholdKeys` presents a valid-looking group key which does not correspond to the victim's secret share. Signing attempts will either fail blame (`complete()` rejects mismatched shares via `verify_share`) or, if verification shares were crafted consistently for attacker shares, produce signatures for the attacker's key. In the worst case — attacker substitutes all `n` shares of a polynomial they know — the reported `group_key` is fully attacker-controlled and deposits are stolen, exactly the BitKeep outcome.

### Likelihood Explanation
Reachability depends on serialized `ThresholdKeys` being sourced from attacker-influenced storage/transport — e.g., a tampered backup, a corrupted/malicious DB write, or a recovery flow. That is precisely the "hijacked package" threat model of the reference incident. Given the format carries a `C::ID` check (curve binding) but no MAC or consistency check, any channel that delivers untrusted bytes to `ThresholdKeys::read` suffices. This makes it a plausible Medium: it requires control over the key-material channel rather than a single network message, but requires no collusion, no leaked keys, and no unsafe code.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), enforce `C::generator() * secret_share == verification_shares[params.i]`, rejecting keys where the share doesn't match its verification share. This cheap `O(1)` check restores the invariant the DKG itself relies on and prevents a substituted byte stream from silently changing the reported `group_key`.

### Proof of Concept
```rust
// Attacker generates their own shamir-style sharing for n participants
let (att_t, att_n) = (2u16, 3u16);
let mut shares = vec![];
let mut vshares = HashMap::new();
for i in 1 ..= att_n {
    let s = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
    shares.push(s);
    vshares.insert(Participant::new(i).unwrap(), Ristretto::generator() * s);
}
// group_key will equal attacker_poly(0)
let attacker_group_key =
    ThresholdKeys::new(ThresholdParams::new(att_t, att_n, Participant::new(1).unwrap()).unwrap(),
        Interpolation::Lagrange, Zeroizing::new(shares[0]), vshares.clone()).unwrap().group_key();

// Attacker serializes these keys but keeps an arbitrary victim secret_share
let mut buf = vec![];
// id_len, id, t, n, i, interpolation byte, victim_share, vshares... per ThresholdKeys::write
let victim_share = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
// ...build bytes per crypto/dkg/src/lib.rs:538-561 with `victim_share` and attacker vshares...

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
// Succeeds: no check that generator * victim_share == vshares[i]
assert_eq!(keys.group_key(), attacker_group_key);
assert_ne!(Ristretto::generator() * *keys.original_secret_share(),
           keys.original_verification_share(keys.params().i()));
// keys.group_key() is attacker-controlled; deposits to it are unspendable by the victim
```

Confidence: the missing consistency check is directly evidenced in `crypto/dkg/src/lib.rs` (read path at lines 573-632, `new` at 349-391). The residual uncertainty is whether the deployed processors ever feed attacker-influenced bytes into `ThresholdKeys::read` — in the inspected processor code it reads from the local DB (`processor/src/key_gen.rs:47-62`), so exploitation requires a channel where serialized key material is attacker-substitutable, matching the "hijacked package" class rather than a purely remote network input.