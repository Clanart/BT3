### Title
Panic on point-at-infinity `R` in attacker-supplied Schnorr signature/verify input causes denial of service - (File: networks/bitcoin/src/crypto.rs)

### Summary
The bug class in GHSA-cvfm-xjc8-f2vm is a parser/verifier that throws a fatal error on malformed input, propagating up and killing the process. The analog in Serai: `x()` in `networks/bitcoin/src/crypto.rs` calls `.expect("point at infinity")`, and `Hram::hram` invokes `x()` on `R` without any prior check. `R` is deserialized from attacker-controlled bytes via `Ciphersuite::read_G`, which performs only a canonical-encoding check and accepts the identity point. Feeding a signature/preprocess whose nonce commitment resolves to the point at infinity into `verify`/`complete` panics the verifying/signing process.

### Finding Description
`x(key)` extracts the x-coordinate and panics on the point at infinity (`networks/bitcoin/src/crypto.rs:13-16`). `x_only` and `Hram::hram` both rely on it, with `hram` documented: "If either `R` or `A` is the point at infinity, this will panic" (`crypto.rs:54-73`).

The `R` value reaches `hram` through two untrusted-input paths:

1. `IetfSchnorr::verify`/`Schnorr::verify` (`crypto.rs:139-150`) computes the challenge via `Hram::hram(&sig.R, ...)`. `sig.R` originates from `SchnorrSignature::read` → `C::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`), which only enforces canonical encoding — the identity element is a canonical, valid encoding and is accepted. A 33-byte compressed encoding of infinity (or the equivalent serialization) passed as `R` triggers `expect("point at infinity")` inside `x()`.

2. Inside `AlgorithmSignatureMachine::complete` (`crypto/frost/src/sign.rs:465`), `self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum)` is invoked with `Rs` computed by `B.nonces(&nonces)` — a sum over per-participant nonce commitments that were read from remote `read_preprocess` bytes (`sign.rs:276-281`). A preprocess whose nonce commitments make the summed `R` equal to the identity also reaches `hram(R=∞)` and panics rather than returning `None`.

In both cases the panic is an unconditional `expect`, not a `FrostError`, so it escapes the `Result` path entirely and aborts the calling task/process — exactly the "system error thrown all the way up" shape of the advisory.

### Impact Explanation
An unprivileged party who can submit bytes to a Schnorr signature verification path (e.g., a crafted BIP-340-style signature, or FROST preprocess bytes producing identity `R`) crashes the verifier/signer. In a node embedding this code without a panic handler, this is a remote denial of service — a single malformed input kills the service. Consistent with the advisory's impact: attacker shuts down services via crafted input.

### Likelihood Explanation
Triggering requires only serializing the identity/invalid `R` — trivially constructible, no secret knowledge needed. Reachability depends on the integrator exposing `verify`/`complete` on untrusted input, which is the normal operating mode for signature verification and multisig completion. The identity check omission in `read_G` is universal across ciphersuites using the default impl.

### Recommendation
Reject the point at infinity (and any non-prime-subgroup point) in `Ciphersuite::read_G` or at minimum in `SchnorrSignature::read`/verify before calling `hram`. Alternatively, make `x()`/`x_only()`/`hram` return `Option`/`Result` and propagate `None` from `verify` instead of panicking. The panic-on-invariant style is only safe if the invariant is enforced at deserialization; it currently is not.

### Proof of Concept
```rust
// Serialize a SchnorrSignature with R = identity (canonical encoding is accepted by read_G)
let mut sig_bytes = Secp256k1::G::identity().to_bytes().as_ref().to_vec();
sig_bytes.extend(<Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref());
let sig = SchnorrSignature::<Secp256k1>::read(&mut sig_bytes.as_ref()).unwrap();

// hram -> x(R) -> expect("point at infinity") panics instead of returning None
let _ = Schnorr::new().verify(group_key, &[vec![sig.R]], sig.s); // panic
```
Equivalently, in FROST `complete`, preprocess commitments chosen so `B.nonces()` sums to identity cause `verify` → `hram` to panic at `crypto.rs:15`.

Caveat: reachability of path (2) through `complete` assumes the summed-R identity isn't filtered upstream in `B.nonces`; path (1) via direct `verify` on deserialized bytes is reachable unconditionally wherever verify is exposed to untrusted input.