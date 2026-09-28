### Title
ThresholdKeys::read adopts fully attacker-supplied key material — including a self-inconsistent secret share and a group key unrelated to any prior DKG — without integrity checks - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` deserializes `t`, `n`, `i`, the interpolation table, the `secret_share` scalar, and all `n` verification shares directly from untrusted bytes, then hands them to `ThresholdKeys::new`, which derives `group_key` solely from `verification_shares[1..=t]` and never checks that `secret_share * G == verification_shares[i]`. A forged serialization is silently materialized into a live, signable `ThresholdKeys` — the direct analog of attacker-controlled bytes being materialized into trusted local state (CVE-2026-48720 bug class).

### Finding Description
`read` (`crypto/dkg/src/lib.rs:574-632`) consumes every field of `ThresholdCore` from the reader: the curve ID check (lines 578-588), `ThresholdParams` (lines 591-602), the `Interpolation` table (lines 604-616), `secret_share` (line 618), and `n` verification shares (lines 620-623). `ThresholdKeys::new` (lines 349-391) only validates counts/participant bounds and the `Constant`-vs-`t!=n` rule, then computes the group key as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` (lines 376-378). Nowhere is it verified that:

- `C::generator() * secret_share == verification_shares[params.i()]` — the deserialized share need not correspond to the deserialized verification shares at all;
- the resulting `group_key` matches any externally attested/expected key from the DKG that produced it.

`view()` (lines 463-533) then signs with this secret share against these verification shares, and `group_key()` (lines 445-447) reports the deserialized group key. Because `read_G` enforces only canonical point encodings (`crypto/ciphersuite/src/lib.rs:91-101`), any well-formed blob is accepted. The serialization is self-describing, so an attacker who can feed bytes to `ThresholdKeys::read` (key-import/restore/coordinator-supplied path, e.g. `GeneratedKeysDb::read_keys` in `processor/src/key_gen.rs:47-62`) fully dictates the identity the node will sign under.

### Impact Explanation
Two concrete outcomes:

1. **Rogue threshold key.** The attacker crafts `secret_share = a`, `verification_shares[l] = a_l * G` for shares `a_l` they choose, interpolating to a group key `K = k*G` whose discrete log `k` the attacker knows. Any funds/deposits addressed to `group_key()` are spendable solely by the attacker, and any signature the node produces under `K` authenticates attacker's key, not the DKG key.

2. **Inconsistent share → mis-blame / signing of an unintended view.** Since `secret_share` is not bound to `verification_shares[i]`, a tampered blob produces a `ThresholdView` whose interpolated share fails verification against the (attacker-set) verification shares of peers, or — with `scalar`/`offset` — yields a `group_key` the caller believes is the multisig key while the actual signing secret differs ("funds reported under a key that is not spendable by the group" / share misattribution).

This is reachable by any party able to supply the serialized bytes; no key compromise is assumed.

### Likelihood Explanation
Deserialization is the standard restore path for key material (the processor stores/loads these blobs via `GeneratedKeysDb`), and `read` performs zero semantic validation beyond canonical encodings — unlike the FROST `Curve::read_G`, which at least rejects identity (`crypto/frost/src/curve/mod.rs:125-131`). Exploitation requires write access to the serialized key blob or a code path feeding attacker bytes to `read`; where that exists, no further primitive is needed. Severity: High — integrity of the node's signing identity depends entirely on an unauthenticated byte format.

### Recommendation
- In `ThresholdKeys::new` (or `read`), verify `C::generator() * secret_share == verification_shares[params.i()]` and reject otherwise.
- Bind serialized keys to their provenance: authenticate the blob (e.g., MAC/signature under a node key, or store the attested `group_key` alongside and compare), so deserialized material cannot silently redefine the signing identity.
- Reject identity verification shares on deserialization.

### Proof of Concept
```rust
// Construct a rogue ThresholdKeys blob for a 2-of-2 "multisig" whose group
// key the attacker fully controls.
let a = C::random_nonzero_F(&mut rng);          // attacker-known share for "us"
let b = C::random_nonzero_F(&mut rng);          // attacker-known share for "peer"
// For t = n = 2 with Lagrange over {1,2}: factor_i makes sum(a_i * lam_i) = k
// Attacker chooses k and solves for a, b s.t. group secret = k.
// Simplest: use Interpolation::Constant([k]) (requires t == n) —
//   group_key = k*G regardless of share placement.

let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes());                 // t
blob.extend(2u16.to_le_bytes());                 // n
blob.extend(1u16.to_le_bytes());                 // i
blob.push(0);                                    // Interpolation::Constant
blob.extend(k.to_repr().as_ref());               // c[0] = k
blob.extend(C::F::ZERO.to_repr().as_ref());      // c[1]
blob.extend(a.to_repr().as_ref());               // secret_share (arbitrary!)
for share in [a, b] {                            // attacker-chosen verif. shares
  blob.extend((C::generator() * share).to_bytes().as_ref());
}

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// keys.group_key() == k*G  — attacker knows k
// keys.params().i() share `a` was never checked against verification_shares[1]
```