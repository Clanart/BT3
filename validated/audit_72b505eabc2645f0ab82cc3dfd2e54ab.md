### Title
Schnorr verification accepts the identity (zero) public key, so a "default/unset" key authenticates any forged signature - (File: crypto/schnorr/src/lib.rs)

### Summary
The Asymmetry M-07 mitigation error is a guard that silently passes when the authorized address is still `address(0)` — the check `manager != 0 && msg.sender != manager` treats "unset" as "authorized". The Serai analog lives in `SchnorrSignature::verify`: the verifier equation `R + c·A − s·G == 0` is evaluated with no check that the public key `A` (or `R`) is non-identity. When `A` is the identity point — the group-element equivalent of `address(0)`, i.e., what an uninitialized or zeroed key deserializes to — the equation degenerates to `R == s·G`, which any unprivileged party satisfies by choosing arbitrary `s` and setting `R = s·G`. Verification under a default/identity key therefore authenticates a forged signature, exactly the "zero value bypasses the authorization check" class.

### Finding Description
`SchnorrSignature::verify` computes `multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity()` at crypto/schnorr/src/lib.rs:108-110. `batch_statements` (lines 88-100) emits `(1, R), (c, A), (−s, G)`. There is no `is_identity`/`is_zero` rejection of `public_key` or `self.R` anywhere in the function, and `SchnorrSignature::read` (lines 51-53) only calls `C::read_G`/`C::read_F`, which enforce canonical encoding but not non-identity (the doc comment at lines 39-41 explicitly notes torsioned/edge points are handled by "strict requirements" that do not include an identity check). With `A = identity`, the statement reduces to `R − s·G == 0`; the attacker picks `s` freely and sets `R = s·G`, producing a signature that verifies for any challenge. The same degenerate case exists in `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs:127-146): a `(identity, c)` entry contributes `z·R_i + z·c·identity = z·R_i`, fully attacker-controlled via `R_i`.

Reachability follows the prescribed surface: untrusted bytes are fed to `read_G`/`ThresholdKeys::read`/`ReceivedOutput::read`/`Commitments::read` and then to a `verify` API. If the deserialized group key is the identity element (e.g., a zeroed/default-encoded key in an untrusted `ThresholdKeys`/`ReceivedOutput` blob, or any code path that treats an absent key as the identity point rather than rejecting it), the resulting `SchnorrSignature::verify`/`SchnorrAggregate::verify` call returns `true` for a forged signature — the same outcome as calling `depositRewards` while `manager == address(0)`.

### Impact Explanation
A forged Schnorr signature is accepted whenever verification runs against a key that deserialized to the identity element. Where such a key guards funds or protocol messages, this yields signature forgery (concrete signing-equivalent impact) rather than a mere best-practice gap.

### Likelihood Explanation
Exploitation requires a path where an attacker-controlled or unset key reaches `verify` as identity. Keys produced by an honest DKG are non-identity, so the exposure window is deserialization of untrusted `ThresholdKeys`/`ReceivedOutput`/signature bytes and any code that substitutes a default identity key for a missing one — plausible but not unconditional, matching Medium severity.

### Recommendation
Reject `public_key.is_identity()` (and `self.R.is_identity()`) in `SchnorrSignature::verify` and in `SchnorrAggregate::verify` for each key/`R_i`, mirroring the fix of requiring `manager != address(0)`; alternatively enforce non-identity in `ThresholdKeys::read`/`Commitments::read` so a default key can never reach verification.

### Proof of Concept
```rust
// crypto/schnorr: forgery under the identity public key
let s = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
let forged = SchnorrSignature::<Ristretto> {
  R: Ristretto::generator() * s,   // R = s·G
  s,
};
let identity_key = <Ristretto as Ciphersuite>::G::identity();
let challenge = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
// identity·c = identity, so the statement is R - s·G == 0 → true
assert!(forged.verify(identity_key, challenge)); // forged signature "valid"
```
Supporting code: `verify` at crypto/schnorr/src/lib.rs:108-110, `batch_statements` at 88-100, no identity check in `read` at 51-53; aggregate equivalent at crypto/schnorr/src/aggregate.rs:138-145.

Caveat: I was unable to fully confirm every in-scope `read_G` implementation accepts the identity encoding (e.g., ed448/schnorrkel backends), and I did not locate a specific production caller that maps "missing key" to the identity point — so the practical trigger depends on a consumer deserializing or defaulting a key to identity before verifying.