### Title
`ThresholdKeys::read` deserializes attacker-controlled threshold parameters and verification shares (including identity points) into a fully attacker-defined group key with no consistency checks - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to CVE-2020-11619's untrusted-deserialization gadget class, `ThresholdKeys::<C>::read` reconstructs a `ThresholdKeys` object — the secret-bearing type used by all FROST signing — from attacker-supplied bytes while performing only structural checks. It never verifies that the deserialized `secret_share` is consistent with `verification_shares[i]`, accepts identity points as verification shares, and lets the input bytes dictate `t`, `n`, `i`, and the interpolation method. The resulting object computes a `group_key` that is entirely under the deserializer's control, and can be a key whose discrete logarithm is known to the attacker.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads, in order, a curve ID, `t`, `n`, `i`, a one-byte interpolation tag (`0` = `Constant(Vec<F>)` of `n` scalars, `1` = `Lagrange`), a raw `secret_share` scalar, and `n` verification-share points — then passes them to `ThresholdKeys::new`. Validation is limited to:

- `ThresholdParams::new` (lib.rs:166-179): `t != 0`, `n != 0`, `t <= n`, `i <= n`.
- `ThresholdKeys::new` (lib.rs:349-378): verification-share count equals `n`, indices `<= n`, and `Constant` interpolation requires `t == n`.

Critically:

1. **Identity points accepted**: shares are read with `<C as Ciphersuite>::read_G` (lib.rs:622), which checks canonicity but does *not* reject the identity — unlike `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131), which explicitly rejects identity. All verification shares, and hence `group_key` (computed at lib.rs:376-378 as `sum(verification_shares[i] * interpolation_factor(i, [1..=t]))`), can be forced to the identity point, whose discrete log is publicly known to be `0`.
2. **No share/verification-share consistency check**: nothing verifies `secret_share * G == verification_shares[i]` or that the shares interpolate to any sensible public key. The `secret_share` is whatever bytes the attacker wrote.
3. **Attacker-controlled parameters**: the attacker chooses `t`, `n`, `i`, the interpolation method, and all `n` verification shares. With `t = 1`, `group_key = verification_shares[1]` — an arbitrary attacker-chosen point with attacker-known discrete log. With `Constant` interpolation (`t == n`), the attacker additionally supplies the per-share coefficient vector used by `interpolation_factor` (lib.rs:226-249).

A deserialized `ThresholdKeys` is then usable via `view()` (lib.rs:463-533) in FROST `SignMachine`s (`AlgorithmSignMachine`/`AlgorithmSignatureMachine` in crypto/frost/src/sign.rs): `group_key()` (lib.rs:445-447) becomes the key signatures are produced and verified under, and `original_verification_shares`/`verification_shares` feed `verify_share` during `complete()`.

### Impact Explanation
An attacker who can cause a victim (or downstream consumer in the in-scope stack — the processor loads `ThresholdKeys` via `ThresholdKeys::read` from stored key material, and the serialization is round-tripped through `write`/`serialize`) to deserialize crafted bytes obtains a key object whose `group_key` they fully control. Concretely:

- Setting `t = 1` and `verification_shares = {1: x*G}` for attacker-known `x` makes `group_key = x*G`. Any address/verification key derived from `group_key()` (e.g., a Bitcoin deposit key via `bitcoin-serai` tweaks) is spendable solely by the attacker — funds reported as received under this key are not controlled by the validator set.
- Identity verification shares force `group_key` to the identity point; signatures under a known-zero discrete log are forgeable by anyone.
- The deserializer can also embed a `secret_share` unrelated to the declared `verification_shares`, yielding a key that passes all `read` checks yet produces shares that fail verification — or, combined with self-consistent attacker shares, makes the attacker the effective sole signer of whatever set `params.i()` participates in.

This is a forged-signature / stolen-funds primitive rooted in deserialization producing a semantically invalid, attacker-defined key object — matching the "untrusted deserialization gadget" bug class.

### Likelihood Explanation
Reachability depends on attacker control of bytes fed to `ThresholdKeys::read`. The serialization contains no authentication tag, MAC, or checksum (lib.rs:538-561), and `read` is a public API consumed by key-loading paths (`GeneratedKeysDb::read_keys` in processor/src/key_gen.rs:57-58 reads it without even an `unwrap` guard beyond `Option`, and the FROST test-vector loader `vectors_to_multisig_keys` demonstrates the format is trivially forgeable by hand). Any path where serialized key bytes cross a trust boundary — key backup/restore, share export, coordinator-supplied key material — is exploitable. Within the crate itself, the input is unconstrained other than the field/point encodings being canonical. Likelihood is bounded by how the integrator sources these bytes, but the missing validation is unconditional in the library code.

### Recommendation
- In `ThresholdKeys::read` / `ThresholdKeys::new`, verify `secret_share * C::generator() == verification_shares[&params.i()]` to ensure the deserialized share is consistent with the declared verification shares.
- Use `Curve::read_G` (identity-rejecting) or explicitly reject identity/small-order verification shares during deserialization.
- Reject `group_key` being the identity, and consider binding `t`/`n`/interpolation to externally authenticated parameters rather than trusting them from the byte stream.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read producing a group key
// whose discrete log they know.
let x = C::F::random(&mut rng);        // attacker-known scalar
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(1u16.to_le_bytes());        // t = 1
buf.extend(1u16.to_le_bytes());        // n = 1
buf.extend(1u16.to_le_bytes());        // i = 1
buf.push(1);                           // Interpolation::Lagrange
buf.extend(x.to_repr().as_ref());      // secret_share = x
buf.extend((C::generator() * x).to_bytes().as_ref()); // verification_shares[1] = x*G

let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap(); // accepted
assert_eq!(keys.group_key(), C::generator() * x); // attacker knows dlog = x
// keys.view(vec![Participant(1)]) -> usable in FROST sign; group key is attacker-controlled.
// Alternatively, write the identity encoding for verification_shares[1]:
// keys.group_key() == identity, discrete log 0, signatures forgeable by anyone.
```
Root cause: crypto/dkg/src/lib.rs:618-631 — `C::read_F` for the secret share and `<C as Ciphersuite>::read_G` (crypto/ciphersuite/src/lib.rs:91-100) for verification shares admit any canonical encoding, including the identity point, with no semantic validation of the assembled key.