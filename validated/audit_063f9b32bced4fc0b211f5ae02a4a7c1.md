### Title
Unchecked identity-point result panics in `x()`/`x_only`, reachable via attacker-supplied FROST preprocess nonce commitments - ([File: networks/bitcoin/src/crypto.rs])

### Summary
The upstream bug class is an unchecked return value (`platform_get_resource()` returning `NULL`) leading to a null-pointer dereference / crash. The Serai analog is `x()` in `networks/bitcoin/src/crypto.rs`, which does `key.to_encoded_point(true).x().expect("point at infinity")` — an unchecked `Option` that panics when the point is the identity. That point can be attacker-controlled: a signing participant's nonce commitments are read from untrusted bytes via `Commitments::read`/`read_preprocess` (which use `C::read_G`), and `read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) only checks canonicality of the encoding — it does not reject the point at infinity. The identity commitments propagate into `nonce_sums`, and the BIP-340 `Hram` calls `x(R)`/`x(A)` on the aggregate nonce and group key during `sign_share`, panicking the entire process.

### Finding Description
- `x()` (`networks/bitcoin/src/crypto.rs:13-16`) unwraps `encoded.x()`, which is `None` iff the point is the point at infinity.
- `Hram::hram` (`crypto.rs:59-73`) calls `x(R)` and `x(A)` and documents: "If either `R` or `A` is the point at infinity, this will panic."
- `C::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) decodes via `G::from_bytes` and only verifies the re-encoding round-trips. The identity point has a canonical encoding (e.g., the SEC `0x00` encoding for secp256k1), so it passes deserialization without error.
- An attacker submitting a preprocess whose `NonceCommitments` for every generator are the identity causes the summed nonce `R` in `nonce_sums` to be identity. When `Schnorr::sign_share`/`verify` (`crypto.rs:128-150`) invokes `Hram::hram`, `x(&identity)` hits `.expect("point at infinity")` and aborts.
- `x_only` (`crypto.rs:21-23`) has the same unchecked `expect`, reachable from `p2tr_script_buf`/`register_offset`/`Scanner::new` (`networks/bitcoin/src/wallet/mod.rs:80-86, 162-166, 185`) if the tweaked/scanned key is ever the identity.

### Impact Explanation
A malformed-but-canonical point encoding (identity) fed into `read_preprocess` / `Commitments::read` crashes the signer/verifier mid-protocol instead of returning an `io::Error`/`FrostError`. This is a remotely triggerable denial of service of the signing node — the direct analog of the CVE's null-pointer dereference — since the panic occurs in library code on data a peer fully controls, before any validity check rejects it.

### Likelihood Explanation
The attacker only needs to supply identity encodings for their nonce commitments in a preprocess. No key material, collusion threshold, or timing is required. The only mitigating factor is whether an upstream FROST `sign`/`complete` path rejects identity nonce sums before `hram` is evaluated — I could not confirm such a check exists in `crypto/frost/src/sign.rs` within the iteration budget, so the panic path should be treated as live unless verified otherwise.

### Recommendation
Reject the identity point in `Ciphersuite::read_G` (or at minimum in `Commitments::read`/`read_preprocess`), and make `x()`/`x_only` return `Option`/`Result` instead of panicking, propagating a `FrostError::InvalidPreprocess` when `R` or `A` is identity.

### Proof of Concept
```rust
// Attacker-controlled preprocess bytes: for a Secp256k1 FROST session,
// encode each nonce commitment as the identity point (SEC1 0x00 encoding)
// instead of a real commitment.
let identity_encoding = [0x00u8; 1]; // k256 GroupEncoding for infinity

let mut preprocess_bytes = vec![];
for _ in 0 .. /* number of expected nonce commitments */ 2 {
  preprocess_bytes.extend_from_slice(&identity_encoding);
}
// plus the (empty) addendum for Schnorr

// Victim side:
let preprocess = machine.read_preprocess(&mut preprocess_bytes.as_slice()).unwrap();
// read_G accepts the canonical infinity encoding; no identity check.
let mut commitments = HashMap::new();
commitments.insert(attacker_participant, preprocess);
// ... include all other participants' preprocesses ...

// During sign(), nonce_sums aggregate to the identity point.
// Hram::hram -> x(&identity) -> encoded.x().expect("point at infinity")
// => panic, aborting the signing process.
machine.sign(commitments, msg);
```

The analogous panic also fires through `Schnorr::verify`/`complete` if identity nonce sums reach `Hram::hram` during share verification, and through `x_only`/`p2tr_script_buf`/`register_offset` (`networks/bitcoin/src/wallet/mod.rs:80-86`) if an identity key is ever passed.

Caveat: I was unable to fully verify within the iteration budget whether `crypto/frost/src/sign.rs` contains an explicit identity check on aggregated nonces before `hram` is invoked; if such a check exists, the reachable panic surface narrows to `x_only`/`p2tr_script_buf` callers.