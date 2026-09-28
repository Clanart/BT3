### Title
`SchnorrSignature::verify` Returns True for a Forged `(R = Identity, s = 0)` Signature Under an Identity Public Key, Which `read_G` Accepts - (File: crypto/schnorr/src/lib.rs)

### Summary
The bug class in the external report is a signature/ownership check that silently accepts the zero value: `ecrecover` returns `address(0)` on failure, which compares equal to a `maker` of `address(0)`, and uninitialized storage (mapping defaults) reads as `address(0)`. The Serai analog lives in `crypto/schnorr`: `SchnorrSignature::verify` performs no identity/zero checks on either the public key or the signature components, and `Ciphersuite::read_G` / `read_F` happily deserialize the identity point and zero scalar. An unprivileged party can therefore supply the byte encoding of the identity point as a public key plus the signature `(R = identity, s = 0)` and have verification return `true` — a forged signature that validates for any challenge.

### Finding Description
`SchnorrSignature::verify` evaluates the multiexp `R + c·A − s·G` and checks it equals the identity. When the attacker supplies a public key `A = identity` (encodable via `C::read_G`, which only checks canonicality of the encoding — `crypto/ciphersuite/src/lib.rs` lines 91-101 — and never rejects the identity) together with `R = identity` and `s = 0` (both deserializable via `SchnorrSignature::read` at `crypto/schnorr/src/lib.rs:51-53`, since `read_F` accepts `0` as a canonical scalar), the statement becomes `identity + c·identity − 0·G = identity`, which is always true regardless of the challenge `c`. This mirrors the report exactly: the zero/identity value is treated as a valid principal, and the equation degenerates to a tautology. `batch_statements`/`verify` at `crypto/schnorr/src/lib.rs:88-110` contain no `is_identity` check on `R` or `s` and no check on `public_key`; the only identity guard in the codebase is in `coordinator/tributary` `Signed::read` (out of scope), which rejects identity `R` — confirming the library itself is where the missing check lives.

### Impact Explanation
Any protocol or verifier that reads an attacker-controlled public key and signature through `read_G` / `SchnorrSignature::read` and calls `verify` (or `batch_verify`, which queues the same statements) will accept a forged signature. The forgery requires no private key, no nonce, and works for every message/challenge — a complete signature forgery, analogous to `tradeValid()` returning true for a zero `maker`.

### Likelihood Explanation
The inputs are fully public: 32/33 zero-or-identity point encodings and a zero scalar encoding. `read_G` accepts the canonical identity encoding (dalek Ristretto and k256 both decode it), and `read_F` accepts zero. Whether a deployment exposes a flow where the public key is attacker-supplied is integration-dependent, but the verification primitive itself — the in-scope code — returns `true` on this input unconditionally.

### Recommendation
Reject the identity in `Ciphersuite::read_G` (or at minimum in `SchnorrSignature::read` for `R`), and reject `s = 0` / identity public keys in `SchnorrSignature::verify` — e.g. `if self.R.is_identity().into() || self.s.is_zero().into() || public_key.is_identity().into() { return false; }` — the same defense as the report's `require(signer != address(0))` / `require(maker != address(0))`.

### Proof of Concept
```rust
// crypto/schnorr against any Ciphersuite (e.g. Ristretto)
// Attacker-controlled bytes: identity point encoding + zero scalar
let mut key_bytes = <Ristretto as Ciphersuite>::G::identity().to_bytes();
let A = Ristretto::read_G(&mut key_bytes.as_ref()).unwrap(); // accepted: identity is canonical

let sig = SchnorrSignature::<Ristretto> {
  R: <Ristretto as Ciphersuite>::G::identity(),
  s: <Ristretto as Ciphersuite>::F::ZERO,
};
// true for ANY challenge: R + c·A − s·G = 0 + c·0 − 0·G = identity
assert!(sig.verify(A, <Ristretto as Ciphersuite>::F::random(&mut OsRng)));
```
Root cause: `verify` (`crypto/schnorr/src/lib.rs:108-110`) checks only the algebraic statement, and `read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) enforces canonicality but not non-identity — the zero-value acceptance the report warns about, in scalar/point form rather than `address(0)` form.