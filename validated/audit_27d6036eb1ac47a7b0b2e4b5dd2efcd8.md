### Title
Attacker-controlled serialized `ThresholdKeys` decouple `group_key` from the secret share, yielding an unusable/foreign group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation mode (including an arbitrary `Constant` coefficient vector), a `secret_share`, and `n` `verification_shares` entirely from attacker-controlled bytes, then hands them to `ThresholdKeys::new`. `ThresholdKeys::new` derives `group_key` solely as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` and never checks that `secret_share` is consistent with `verification_shares[i]` (i.e., `C::generator() * secret_share == verification_shares[i]`), nor that `verification_shares` form a polynomial commitment to any secret at all. The result mirrors the Craft CMS bug class: untrusted configuration bytes are instantiated into a fully-formed object graph without a cleansing/consistency pass, letting the supplier redefine the object's effective behavior (its declared group key) independently of the secret material it purports to hold.

### Finding Description
- `ThresholdKeys::read` reads `interpolation` tag `0` as `Interpolation::Constant` with `n` caller-chosen scalars, tag `1` as `Lagrange`, then reads `secret_share` and `n` verification shares with no cross-field validation (crypto/dkg/src/lib.rs:604-632).
- `ThresholdKeys::new` enforces `verification_shares.len() == n`, participant indexes `<= n`, and `Constant => t == n`, but computes `group_key` only from `verification_shares[1..=t]` and the attacker-chosen interpolation coefficients, with no binding to `secret_share` (crypto/dkg/src/lib.rs:349-391).
- `interpolation_factor` for `Constant` indexes `c[i-1]` directly, so every coefficient is attacker-controlled (crypto/dkg/src/lib.rs:226-249).
- Downstream, `group_key()` feeds `tweak_keys`, `Scanner::new`/`register_offset`, and the FROST binding transcript (`rho_transcript.append_message(b"group_key", ...)` in crypto/frost/src/sign.rs:363), so the forged key identity propagates to address derivation and signing.
- Because `secret_share` need not match `verification_shares[i]`, `view()` produces a `ThresholdView` whose interpolated `secret_share` is inconsistent with the interpolated `verification_shares`; any signature share produced will fail `verify_share`, and recovery via `recover_key` fails its `G * res == group_key` check (crypto/dkg/recovery/src/lib.rs:80-82).

### Impact Explanation
An unprivileged party who can supply `ThresholdKeys::read` input (a listed reachable surface) can cause the host to adopt a `group_key` of the attacker's choosing while holding a `secret_share` unrelated to it. Concretely: the attacker sets `verification_shares` (and `Constant` coefficients when `t == n`) so the derived `group_key` is a key under their control or simply inconsistent with the loaded share. Funds sent to the Taproot address derived via `tweak_keys`/`p2tr_script_buf`/`Scanner` from this `group_key` are reported received but cannot be spent — every FROST session produces shares that fail `verify_share` against the manipulated `verification_shares`, and `recover_key` aborts. Alternatively the attacker pins `verification_shares` consistent with the victim's share under a different polynomial so signatures remain internally valid yet for a `group_key` different from the real DKG key, causing the node to sign for a key it never agreed to.

### Likelihood Explanation
Exploitation requires an attacker to be able to feed serialized `ThresholdKeys` to a target (backup/import path, peer-supplied key material, or any flow where key bytes cross a trust boundary). No secret knowledge is needed — the crafted blob is self-consistent except for the share-vs-share-commitment mismatch the code never checks. Impact requires the loaded keys to be used for scanning or signing; where that occurs, the outcome is deterministic fund lock or wrong-key signatures.

### Recommendation
In `ThresholdKeys::new` (and thereby `ThresholdKeys::read`), reject inputs where `C::generator() * secret_share != verification_shares[&params.i()]`. Optionally require `verification_shares` to hash to a stored group-key commitment or to satisfy a low-degree consistency check so `group_key` cannot be set independently of the share. This is the analogue of the missing `cleanseConfig()` call: sanitize the instantiated object's consistency at the construction boundary, before `group_key` is consumed by `tweak_keys`, `Scanner`, or the FROST `rho` transcript.

### Proof of Concept
```rust
// Craft serialized ThresholdKeys over e.g. Secp256k1 (frost::curve::Secp256k1)
// where group_key != G * actual_secret:
let mut buf = vec![];
buf.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
buf.extend(Secp256k1::ID);
buf.extend(2u16.to_le_bytes());          // t = 2
buf.extend(2u16.to_le_bytes());          // n = 2
buf.extend(1u16.to_le_bytes());          // i = 1
buf.push(0);                             // Interpolation::Constant
buf.extend(Scalar::ONE.to_repr());       // c[1]
buf.extend(Scalar::ONE.to_repr());       // c[2]
buf.extend(victim_share.to_repr());      // secret_share = victim's real share
// verification_shares chosen so sum yields attacker-chosen group_key A*G:
//   v[1] = A*G - v[2]; v[2] = arbitrary
buf.extend((A * G - V2).to_bytes());
buf.extend(V2.to_bytes());

let keys = ThresholdKeys::<Secp256k1>::read(&mut buf.as_slice()).unwrap();
// keys.group_key() == attacker-chosen A*G even though secret_share is unrelated.
// tweak_keys(keys) -> Scanner address reports deposits to A*G as received;
// FROST shares from secret_share fail verification_share checks -> unspendable.
```
Root cause: `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) computes `group_key` from attacker-supplied `verification_shares`/coefficients without asserting `C::generator() * secret_share == verification_shares[&params.i()]`, and `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reaches it purely from untrusted bytes.