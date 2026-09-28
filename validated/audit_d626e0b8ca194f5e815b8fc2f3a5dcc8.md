### Title
Deserializing a crafted `ThresholdKeys` file yields inconsistent keys whose secret share, verification shares, and group key are independently attacker-controlled - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`) reconstructs signing keys entirely from untrusted bytes: `t`, `n`, `i`, the interpolation variant (including a `Vec` of constant coefficients), the `secret_share` scalar, and every `verification_shares` point. It delegates validation to `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349`), which only checks the count and index range of the verification shares, `t <= n`, and `t == n` for `Interpolation::Constant`. It never checks that `secret_share * C::generator() == verification_shares[params.i()]`, i.e., that the loaded secret share is actually consistent with the loaded public verification shares. It also derives `group_key` purely from the attacker-supplied `verification_shares[1..=t]` and attacker-supplied interpolation coefficients (`crypto/dkg/src/lib.rs:376-378`).

### Finding Description

Every field of a serialized `ThresholdKeys` is attacker-controlled plaintext (the write path at `crypto/dkg/src/lib.rs:538-561` emits `C::ID`, params, interpolation, `secret_share`, and the verification shares with no MAC, checksum, or binding). On read:

1. `ThresholdParams::new` accepts any `t <= n`, `i <= n` (`crypto/dkg/src/lib.rs:166-179`).
2. For `Interpolation::Lagrange`, `Interpolation::Constant` bypassed, no consistency check at all applies.
3. `group_key` is computed as `sum(verification_shares[l] * interpolation_factor(l, 1..=t))` — fully derived from attacker bytes.
4. `secret_share` is stored verbatim; nothing binds it to `verification_shares[i]` or to `group_key`.

Subsequent use via `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:463-533`) interpolates the attacker-chosen `secret_share` and hands it to FROST signing (`crypto/frost`), while `group_key()` returns the attacker-chosen key. The honest PedPoP path always produces consistent keys (its own share is `G * secret` at `crypto/dkg/pedpop/src/lib.rs:516`), so the missing check is only exploitable on the deserialize path — the exact analog of loading a maliciously crafted checkpoint.

### Impact Explanation

A crafted file produces `ThresholdKeys` where the reported `group_key` is an attacker-selected public key while the embedded `secret_share` is an attacker-selected scalar. Any downstream consumer that loads these keys and signs (e.g., feeds the resulting `ThresholdView` into FROST `sign`/`complete`) produces signatures under an attacker-defined secret share for an attacker-defined group key — the validator signs under a key it never generated, enabling signing of unintended key material and reporting a group key / receive addresses (Bitcoin outputs scanned for `group_key`-derived script_pubkeys) that are not actually spendable by the honest threshold. This is deterministic and requires only that attacker bytes reach `ThresholdKeys::read`, which is a listed in-scope deserialization sink.

### Likelihood Explanation

Reachable by any party able to supply the serialized key bytes (a crafted keys file/blob, matching the CVE's "convince a user to load a malicious file" primitive). No cryptographic break is needed; the only requirement is that `read` output is trusted for signing, which is the documented purpose of the API. Moderated only by the need for an integrator to load externally supplied bytes.

### Recommendation

In `ThresholdKeys::new` (or at the end of `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[params.i()]` and reject otherwise. Additionally, for `Interpolation::Constant`, bound the coefficient vector length to `n` (it is read as `n` scalars, but `interpolation_factor` indexes `c[i - 1]` with no length check beyond `t == n`, so mismatched files deserve an explicit check). Consider authenticating serialized key blobs at rest.

### Proof of Concept

```rust
// Ristretto context; bytes fed to ThresholdKeys::<Ristretto>::read
let mut buf = vec![];
buf.extend((Ristretto::ID.len() as u32).to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes()); // t = 1
buf.extend(1u16.to_le_bytes()); // n = 1
buf.extend(1u16.to_le_bytes()); // i = Participant(1)
buf.push(1);                    // Interpolation::Lagrange

// attacker-chosen secret_share scalar
let evil_share = <Ristretto as Ciphersuite>::F::ONE;
buf.extend(evil_share.to_repr().as_ref());

// verification_shares[1] = attacker-chosen point, unrelated to evil_share
let attacker_key = Ristretto::generator() * <Ristretto as Ciphersuite>::F::from(2u64);
buf.extend(attacker_key.to_bytes().as_ref());

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
// Succeeds despite verification_shares[1] != G * secret_share:
// group_key() == attacker_key, original_secret_share() == ONE
assert_eq!(keys.group_key(), attacker_key);          // attacker-selected group key
assert_ne!(
  Ristretto::generator() * **keys.original_secret_share(),
  keys.original_verification_share(Participant::new(1).unwrap()),
); // inconsistency never detected
```

`ThresholdKeys::new` returns `Ok`, `view(vec![Participant::new(1).unwrap()])` yields a `ThresholdView` whose `secret_share` is `ONE` while `group_key` is `attacker_key`, and FROST `sign` then produces partial signatures under a key pair the honest DKG never produced.