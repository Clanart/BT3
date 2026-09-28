### Title
`IetfTranscript::rng_seed` is unimplemented, causing `IetfSchnorr` signing to panic - (File: crypto/frost/src/algorithm.rs)

### Summary
The IETF-compatible transcript provided by the FROST crate (`IetfTranscript`, used by the publicly-exported `IetfSchnorr` algorithm) leaves `Transcript::rng_seed` as `unimplemented!()`. The FROST signing machine derives signer nonces via `Curve::random_nonce`, which hashes a seed obtained from the transcript's `rng_seed` together with the participant's secret. Consequently, any caller that constructs the documented IETF-compatibility algorithm via `Schnorr::ietf()` and drives it through `PreprocessMachine::preprocess` / `AlgorithmMachine` will hit the `unimplemented!()` panic instead of producing nonce commitments. This is a direct analog of the report: a function that is declared/published but never implemented, such that a downstream function which depends on it cannot operate.

### Finding Description
`IetfTranscript` is exposed through `pub type IetfSchnorr<C, H> = Schnorr<C, IetfTranscript, H>` with a public constructor `Schnorr::ietf()` documented for IETF compatibility (`crypto/frost/src/algorithm.rs:155-171`). Its `Transcript` implementation stubs out `rng_seed` with `unimplemented!()` (`crypto/frost/src/algorithm.rs:122-124`):

```rust
// FROST won't use this and this shouldn't be used outside of FROST
fn rng_seed(&mut self, _: &'static [u8]) -> [u8; 32] {
  unimplemented!()
}
```

The comment claims FROST won't use it, yet the nonce-derivation pipeline in the FROST signing machines obtains its seed material from the algorithm transcript's `rng_seed` (the seed is hashed with the secret inside `Curve::random_nonce`, as `d + (e * p)` combination happens downstream in `sign_share`). Any session instantiated with `IetfSchnorr` therefore panics when the preprocess stage requests the nonce seed — before any signature share can be produced.

### Impact Explanation
Signing sessions built on the IETF-compatible algorithm cannot complete: `preprocess` panics instead of returning `(SignMachine, Preprocess)`. All functions layered on top — threshold Schnorr signing for the IETF vector-compatible path, test vectors exercising `ietf()`, and any downstream consumer choosing `IetfSchnorr` for interoperability — are non-functional. No secret material is at risk, mirroring the original finding's impact class (function of the protocol broken, no value at risk → Medium).

### Likelihood Explanation
`IetfSchnorr` and `Schnorr::ietf()` are publicly exported and explicitly documented as the path for IETF-draft compatibility, so any deployment or test that selects them deterministically hits the panic on the first preprocess. Reachability does not depend on adversarial input — it triggers on normal use of a supported API.

### Recommendation
Implement `IetfTranscript::rng_seed` in an IETF-consistent manner (e.g., hashing the accumulated transcript buffer under an H3-style domain separation to produce the 32-byte seed), or remove/deprecate `IetfSchnorr`/`ietf()` if the IETF nonce-generation flow is intentionally unsupported, so callers cannot construct an algorithm guaranteed to panic.

### Proof of Concept
```rust
use frost::{curve::Secp256k1, sign::*, algorithm::*, dkg::tests::key_gen};
// Any dkg-produced keys; ietf() selects IetfTranscript
let keys = /* ThresholdKeys<Secp256k1> from key_gen */;
let machine = AlgorithmMachine::<Secp256k1, IetfSchnorr<Secp256k1, _>>::new(
  Schnorr::ietf(), keys,
);
// preprocess -> transcript.rng_seed -> unimplemented!() -> panic
let (_sign_machine, _preprocess) = machine.preprocess(&mut rand::thread_rng());
```
Expected result: panic at `crypto/frost/src/algorithm.rs:123` inside `IetfTranscript::rng_seed`, instead of a valid preprocess.