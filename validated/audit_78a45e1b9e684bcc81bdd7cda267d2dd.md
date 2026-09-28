### Title
`ThresholdKeys::read` accepts a fully attacker-supplied share set without verifying `secret_share` ↔ `verification_shares[i]` consistency or group-key provenance - (File: crypto/dkg/src/lib.rs)

### Summary
The FeehiCMS report describes an unrestricted-upload flaw: attacker-controlled bytes are accepted as a privileged artifact (an executable/avatar) with no validation of what they actually are. The Serai analog is the deserialization of a `ThresholdKeys` blob. `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) ingests attacker-controlled `t`, `n`, `i`, interpolation coefficients, `secret_share`, and all `n` `verification_shares`, then hands them to `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391), which *derives* `group_key` from `verification_shares[1..=t]` and stores everything — but never checks that `C::generator() * secret_share == verification_shares[i]`, that the shares interpolate to any externally-expected group key, or rejects identity verification shares (`Ciphersuite::read_G`, crypto/ciphersuite/src/lib.rs:91-101, does not reject identity; only `Curve::read_G` does, crypto/frost/src/curve/mod.rs:125-131).

### Finding Description
`read` validates only structural bounds: `Participant::new` rejects `i = 0` and `ThresholdParams::new` rejects `t = 0`, `n = 0`, `t > n`, `i > n` (crypto/dkg/src/lib.rs:166-179). `ThresholdKeys::new` only checks the count/index bounds of the share map and `t == n` for `Constant` interpolation (crypto/dkg/src/lib.rs:355-374). Nothing binds the deserialized `secret_share` to `verification_shares[i]`, and `group_key` is whatever the attacker's shares interpolate to:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
(crypto/dkg/src/lib.rs:376-378)

Downstream, `view()` consumes this state verbatim: it interpolates `secret_share` into the signing share and scales `verification_shares` (crypto/dkg/src/lib.rs:494-521), and `group_key()` returns `core.group_key * scalar + G * offset` (crypto/dkg/src/lib.rs:445-447). FROST `sign` will emit a signature share that fails `verify` against the interpolated `verification_shares[i]` whenever the blob's `secret_share` doesn't match the claimed verification share — and the coordinator/verifier will attribute the failure to this participant (blame).

Two concrete attacker wins from supplying a crafted blob:

1. **Honest-party blame/slashing:** attacker keeps the real verification shares but swaps in an arbitrary `secret_share`. `view()` produces a wrong signing share; every FROST partial signature the node emits fails verification and is blamed on the victim participant, enabling false attribution/slashing and a signing liveness failure for the whole set.
2. **Unspendable deposits:** attacker crafts shares so the derived `group_key` is a point under their control (or inconsistent with the actual DKG output). The scanner/wallet (`group_key()` → Bitcoin address derivation) credits deposits to that key while the loaded `secret_share` cannot sign for it — funds reported received that are not spendable.

### Impact Explanation
An attacker who can feed the serialized-keys blob (the "uploaded file" analog — the rules bless `ThresholdKeys::read` as an untrusted-byte sink) controls the node's effective threshold identity. Consequences reachable from public inputs: persistent emission of invalid FROST shares that are cryptographically blamed on the victim participant (fraudulent fault attribution/slashing, Medium), and routing of deposits to an attacker-dictated `group_key` the node cannot spend from (Medium). No secret is leaked, but incorrect object state is accepted as authoritative, matching the CWE-434 class of accepting unvalidated attacker artifacts.

### Likelihood Explanation
Reachability requires the blob to reach `ThresholdKeys::read` — i.e., an attacker-controlled keys file/backup/transfer artifact rather than live network traffic, which lowers the practical likelihood versus a directly-networked parser. Once loaded, the failure is deterministic: no probabilistic conditions, race, or additional attacker action is needed; the first signature attempt (or first deposit scan) exposes the corrupted state. Severity is Medium: integrity of blame attribution and fund custody are affected, but key material itself is not extracted.

### Recommendation
In `ThresholdKeys::new` (or at the end of `ThresholdKeys::read`), add a consistency check `if C::generator() * *secret_share != verification_shares[&params.i()] { Err(...) }`, reject identity verification shares, and, where an expected group key exists, verify `group_key` matches it rather than silently deriving it from untrusted shares. Document that the blob must come from authenticated storage, or include a MAC/commitment over the serialized keys.

### Proof of Concept
```rust
// Attacker blob for a real t-of-n set, preserving public verification_shares
// but substituting an arbitrary secret_share s' != share_i.
let mut blob = vec![];
blob.extend((C::ID.len() as u32).to_le_bytes());
blob.extend(C::ID);
blob.extend(t.to_le_bytes());
blob.extend(n.to_le_bytes());
blob.extend(i.to_bytes());              // victim's real index
blob.push(1);                           // Interpolation::Lagrange
blob.extend(arbitrary_scalar.to_repr());// s' — no consistency check exists
for l in 1..=n {
    blob.extend(real_verification_shares[l].to_bytes()); // real public shares
}
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap(); // accepted
let view = keys.view(included).unwrap();
// Frost signing uses view.secret_share() = lambda_i * s', but
// view.verification_share(i) = G * share_i * lambda_i.
// Every emitted signature share fails Schnorr verify -> blame -> slash,
// while the attacker-provided blob is never flagged as malformed.
```
The check that is missing is a one-line `G * secret_share == verification_shares[i]` assertion in `ThresholdKeys::new`; its absence is the whole bug.