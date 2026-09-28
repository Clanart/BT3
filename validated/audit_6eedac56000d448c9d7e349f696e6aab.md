### Title
`ThresholdKeys::read` accepts attacker-crafted keys with an arbitrary group key and a `secret_share` unbound to `verification_shares[i]` - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the report's "lack of input validation" class, `ThresholdKeys::read` deserializes `t`, `n`, `i`, an interpolation variant, a `secret_share`, and `n` verification shares, then passes them to `ThresholdKeys::new`, which performs only count/bounds checks — it never verifies that `secret_share` is consistent with `verification_shares[i]` (i.e. `generator() * secret_share == verification_shares[i]`), nor that the group key it computes corresponds to any valid sharing. The group key is derived solely from the first `t` attacker-supplied verification shares.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, the interpolation byte (0 = `Constant` with `n` scalars, 1 = `Lagrange`), a `secret_share` via `C::read_F`, and a verification share for each of `1 ..= n` via `C::read_G`, then calls `ThresholdParams::new` and `ThresholdKeys::new`. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) only checks that `verification_shares.len() == n`, that all participant indexes are `<= n`, and that `t == n` when `Interpolation::Constant` is used. It then computes `group_key` as the interpolated sum of `verification_shares[1 ..= t]` — points chosen entirely by whoever serialized the blob.

There is no check anywhere in this path that:
- `verification_shares[params.i()] == C::generator() * *secret_share` (own share consistent with own public share), or
- the verification shares form a coherent polynomial at all (they don't need to — `group_key` is just whatever the first `t` points interpolate to).

`ThresholdKeys::read` is a deserialization entry point reachable with untrusted bytes (e.g., key material supplied during recovery/import flows built on the in-scope `dkg`/`recovery` code). A malicious party that supplies a serialized `ThresholdKeys` blob can therefore install a key set on a victim where:

- `group_key()` is a point whose discrete logarithm the attacker knows — the attacker sets each `verification_shares[j] = generator() * a_j` for `j = 1 ..= t` with known `a_j`, so `group_key = generator() * Σ λ_j·a_j` is a key the attacker controls outright.
- The victim's `secret_share` is unrelated to `verification_shares[i]`, so any FROST `ThresholdView` the victim produces (`view()` at crypto/dkg/src/lib.rs:463-533 interpolates `secret_share` and verifies against these shares) yields signature shares that fail verification against the attacker-committed verification shares.

### Impact Explanation
If a victim imports attacker-supplied serialized `ThresholdKeys` (the documented sink), the reported `group_key()` — the address/wallet key any integrator would derive from these keys — is fully controlled by the attacker. Any funds sent to that key are spendable by the attacker and effectively unspendable by the victim: the victim's secret share does not correspond to the group key, so honest signing attempts produce invalid shares. This is a "funds reported received that are not spendable (by the victim)" outcome rooted purely in missing input validation on a deserialization path, matching the report's bug class of unchecked configuration/parameters.

### Likelihood Explanation
Exploitation requires the victim to deserialize a `ThresholdKeys` blob provided by an untrusted party (e.g., a recovery blob or key package transmitted by a peer rather than generated locally). `ThresholdKeys::read` is exposed as a public API accepting arbitrary byte streams, and nothing in the format carries a proof binding `secret_share` to the verification shares. Where keys are only ever self-generated and self-serialized the issue is latent, but any import/recovery flow over untrusted bytes hits it deterministically — no computation or race is needed, just a malformed blob.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), add the missing consistency validation:
- Reject inputs where `verification_shares[params.i()] != C::generator() * *secret_share`.
- For `Interpolation::Constant`, also check `coefficients.len() == n` (currently only `t == n` is enforced; `interpolation_factor` indexes `c[i - 1]`, so a short vector would panic — crypto/dkg/src/lib.rs:226-228).
- Consider rejecting identity/invalid verification shares by reading them with the identity-rejecting `Curve::read_G` semantics rather than the plain `Ciphersuite::read_G`.
- Document that `ThresholdKeys::read` must not be used on untrusted input, or add an authentication/integrity layer (e.g., a MAC or transcript binding) over serialized key material so a swapped-in blob fails loudly.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC over Ristretto
use dkg::{Participant, ThresholdKeys, ThresholdParams, Interpolation};
use dalek_ff_group::{Ristretto, Scalar};
use ciphersuite::{group::{ff::Field, GroupEncoding}, Ciphersuite};
use zeroize::Zeroizing;
use std::collections::HashMap;

// Attacker crafts a serialized ThresholdKeys blob (same layout as ThresholdKeys::read):
//   u32 id_len || C::ID || u16 t || u16 n || u16 i || u8 interp || F secret_share || n * G shares
let t = 2u16; let n = 3u16; let i = Participant::new(1).unwrap();

// Attacker-chosen verification shares with KNOWN discrete logs a_j,
// so the resulting group_key = G * Σ λ_j·a_j is fully controlled by the attacker.
let known: Vec<Scalar> = (0 .. n).map(|_| Scalar::random(&mut rand_core::OsRng)).collect();
let verification_shares: HashMap<Participant, _> = (1 ..= n)
    .map(|j| (Participant::new(j).unwrap(), Ristretto::generator() * known[j as usize - 1]))
    .collect();

let mut blob = vec![];
blob.extend((Ristretto::ID.len() as u32).to_le_bytes());
blob.extend(Ristretto::ID);
blob.extend(t.to_le_bytes());
blob.extend(n.to_le_bytes());
blob.extend(1u16.to_le_bytes());
blob.push(1); // Interpolation::Lagrange
blob.extend(Scalar::random(&mut rand_core::OsRng).to_repr().as_ref()); // arbitrary secret_share
for j in 1 ..= n {
    blob.extend(verification_shares[&Participant::new(j).unwrap()].to_bytes().as_ref());
}

let keys = ThresholdKeys::<Ristretto>::read(&mut blob.as_slice()).unwrap();

// 1) group_key is attacker-controlled: attacker knows its discrete log
//    dlog = Σ λ_j * a_j over participants 1..=t.
// 2) verification_shares[i] != G * secret_share — never checked:
//    keys.view(vec![1,2]) produces shares that fail verification under
//    verification_shares[1], so the victim can never sign for group_key,
//    while any deposit to group_key is spendable only by the attacker.
```

Root cause: `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) performs no binding between the deserialized `secret_share`, the `verification_shares`, and the derived `group_key`; `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) checks only counts and index bounds, so "fail early and loudly" validation of the key material's internal consistency is absent.