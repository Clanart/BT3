### Title
Malformed serialized `ThresholdKeys` with identity group key panics the signer during `Hram`/`x_only` - (File: networks/bitcoin/src/crypto.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) deserializes attacker-controlled `t`, `n`, `i`, the interpolation mode, the secret share, and `n` verification shares, then calls `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391). `ThresholdKeys::new` computes `group_key` as the interpolated sum of the first `t` verification shares (lib.rs:376-378) but never checks that this sum is non-identity. An attacker who can feed crafted bytes to `ThresholdKeys::read` (e.g., a malicious dealer/dealer-adjacent party supplying key material) can choose verification shares whose interpolated sum is the point at infinity — trivially, by setting the `t`-th share to the negation of the sum of shares `1..t` — even though each individual point is a valid, non-identity encoding that `read_G` accepts.

When these keys are later used for signing, `ThresholdView::group_key()` returns the identity point (lib.rs:445-447), and the FROST signing path invokes the Bitcoin `Hram::hram` implementation, which calls `x(A)` / `x(R)` (networks/bitcoin/src/crypto.rs:59-72). `x()` does `key.to_encoded_point(true)` then `encoded.x().expect("point at infinity")` (crypto.rs:13-16), and `x_only` does `XOnlyPublicKey::from_slice(...).expect(...)` (crypto.rs:21-23). Both panic on the identity point, which is exactly what the code documents: "If either `R` or `A` is the point at infinity, this will panic" (crypto.rs:54).

This is the direct analog of CVE-2024-2199: malformed, attacker-supplied input accepted by a deserialization path causes a panic (DoS) in the operation that consumes it.

### Finding Description
- `ThresholdKeys::read` accepts any `t`, `n`, `i` satisfying `ThresholdParams::new` and any `n` group elements accepted by `read_G`, then constructs keys without validating the derived `group_key` (`crypto/dkg/src/lib.rs:574-632`, `349-391`).
- `Interpolation::Constant`/`Lagrange` `interpolation_factor` (lib.rs:226-249) means the attacker controls the group key both via coefficient choice and share choice.
- The panic sites are `x()` (networks/bitcoin/src/crypto.rs:15) and `x_only()` (crypto.rs:22), reached from `Hram::hram` (crypto.rs:59-73) inside `AlgorithmSignMachine::sign`/`sign_share` (crypto/frost/src/sign.rs) and from `Schnorr::verify` (crypto.rs:139-150).
- Reaching the panic only requires that the crafted `ThresholdKeys` be accepted and a signing attempt be initiated; no secret knowledge is needed to construct shares summing to identity.

### Impact Explanation
A single maliciously crafted serialized `ThresholdKeys` blob causes a deterministic panic in every signing attempt that uses it — aborting the signer thread/process rather than returning `FrostError`. Because `read` returns `Ok` for these keys (all error paths are mapped through `io::Error`, lib.rs:625-631), the malformed input is accepted silently and the crash is deferred to first use, making attribution harder. Depending on deployment, a panic in the signing path can halt a validator's signing duties. Severity: Medium (authenticated/adjacent-party supply of key bytes, availability impact only, consistent with the CVSS 5.7 class of the source advisory).

Additionally, an identity group key corresponds to group secret 0, so if the panic were avoided the resulting key would be trivially forgeable — the missing validation is a semantic-key-validity gap, not just a crash.

### Likelihood Explanation
Requires an attacker to get crafted bytes into `ThresholdKeys::read` — i.e., supply or corrupt serialized threshold key material (e.g., during a malicious-DKG flow or restore path), which the in-scope rules explicitly allow as untrusted input to `ThresholdKeys::read`. Constructing the input is trivial (choose `n-1` arbitrary valid points and set one share to the negated sum); probability of accidental occurrence is negligible, so this is a deliberate-attack scenario. Once accepted, triggering is deterministic on the next `sign`/`verify` call.

### Recommendation
- In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391), reject a `group_key` that is the point at infinity (e.g., `if bool::from(group_key.is_identity()) { Err(...) }`) so malformed keys fail at construction/read time.
- Prefer returning errors over `expect`/`panic` in `x()`/`x_only()` (networks/bitcoin/src/crypto.rs:13-23), or pre-check `R`/`A` for identity in `Hram::hram` and `Schnorr::verify` and surface a `FrostError`/`None` instead.

### Proof of Concept
```rust
// Attacker crafts serialized ThresholdKeys<Secp256k1> bytes:
//   id_len || "Secp256k1" || t=2 || n=2 || i=1 || interpolation=1 (Lagrange)
//   || secret_share (any F) || share[1] = G || share[2] = -G
// read() succeeds; group_key = G + (-G) = identity.

let keys = ThresholdKeys::<Secp256k1>::read(&mut crafted_bytes.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // accepted silently

// Any signing attempt panics:
let machine = AlgorithmMachine::new(Schnorr::new(), keys)
    .preprocess(&mut rng);          // ok
// peer preprocess arrives; sign() -> hram(R, A = identity) ->
//   x(A).expect("point at infinity") -> panic
```

Caveat: I verified the panic sites and the missing `group_key` validation, but did not fully confirm whether `read_G` for `Secp256k1` rejects identity encodings per-share — the attack works regardless, since only the *sum* must be identity and each share is a distinct valid non-identity point.