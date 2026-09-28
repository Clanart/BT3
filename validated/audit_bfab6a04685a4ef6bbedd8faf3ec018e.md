### Title
Identity public key accepted by `SchnorrSignature::verify` enables trivial universal forgery - (File: crypto/schnorr/src/lib.rs)

### Summary
The reported bug class is a missing zero-value check on a security-critical field (the zero `to` address), letting an attacker steer effects toward a value nobody controls. In Serai, the same shape exists for Schnorr signatures: `SchnorrSignature::read` and `SchnorrSignature::verify` never reject the identity group element, and the generic `Ciphersuite::read_G` they rely on explicitly does not reject identity either. As a result, the "zero" public key (point at infinity) is a fully usable key for which anyone can forge a signature that always verifies.

### Finding Description
`Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only enforces canonical encoding; it does not reject the identity point. `SchnorrSignature::read` (crypto/schnorr/src/lib.rs:51-53) uses `C::read_G` for `R` and `C::read_F` for `s` with no identity/zero check — unlike FROST's `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131), which does reject identity, and unlike `coordinator/tributary/src/transaction.rs:63-69`, where Serai itself had to bolt on an ad-hoc `signature.R.is_identity()` rejection with the comment that it "should never come up". The verification equation in `batch_statements`/`verify` (crypto/schnorr/src/lib.rs:88-110) checks `R + c·A − s·G == identity`. When the caller-supplied `public_key` is identity, any pair `(R, s)` satisfying `R = s·G` verifies — no private key or discrete log needed. Both `public_key` and the signature are attacker-controlled bytes reachable through `C::read_G` / `SchnorrSignature::read` + `verify`, which are exactly the untrusted-input surfaces in scope.

### Impact Explanation
Any consumer that reads a public key through `Ciphersuite::read_G` (which accepts identity) and then calls `SchnorrSignature::verify` or `batch_verify` will accept a forged signature "from" the identity key — a key analogous to the zero address, owned by no one. That is a forged signature accepted by the verifier, satisfying the highest-impact class here. The fact that Serai's own tributary code had to special-case identity `R` demonstrates the library-level gap is real and was known to matter.

### Likelihood Explanation
Requires a downstream verifier that (a) deserializes attacker-supplied public keys via `Ciphersuite::read_G` or otherwise permits the identity key into `verify`/`batch_verify`/`aggregate` verification, and (b) attributes meaningful authority to that key. The primitives provide no defense (the identity-rejecting `Curve::read_G` is only used on FROST paths); the exploit itself is trivial (pick `s`, set `R = s·G`). Consistent with the source report's Medium severity: the flaw is conditional on how keys are sourced, but the forgery is free once reachable.

### Recommendation
Reject the identity point in `SchnorrSignature::read` (as `Signed::read` already does locally in `coordinator/tributary/src/transaction.rs`) and reject identity `public_key` inside `SchnorrSignature::verify`/`batch_verify`/`batch_statements`. Alternatively — or additionally — reject identity in `Ciphersuite::read_G`, matching the stricter `Curve::read_G` behavior, so no caller can deserialize the "zero key" as a valid participant key. This is the direct analog of `require(to != address(0))`.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dalek_ff_group::{Scalar, EdwardsPoint, Ed25519};
use schnorr::SchnorrSignature;

// Identity public key: Ed25519 identity compresses to 0x01 followed by 31 zero
// bytes, which is a canonical encoding accepted by Ciphersuite::read_G.
let identity_key_bytes = {
    let mut b = [0u8; 32];
    b[0] = 1;
    b
};
let public_key: EdwardsPoint =
    Ed25519::read_G::<&[u8]>(&mut identity_key_bytes.as_ref()).unwrap(); // succeeds
assert!(bool::from(public_key.is_identity()));

// Forge a signature under the identity key: pick any s, set R = s*G.
// Verification checks R + c*A - s*G == identity; with A = identity this is
// R - s*G == identity, satisfied by construction for ANY challenge c.
let s = Scalar::from(42u64);
let forged = SchnorrSignature::<Ed25519> { R: Ed25519::generator() * s, s };
assert!(forged.verify(public_key, Scalar::from(1337u64)));

// Even the degenerate (R = identity, s = 0) form passes read + verify:
let mut sig_bytes = identity_key_bytes.to_vec();
sig_bytes.extend([0u8; 32]); // s = 0, canonical scalar
let sig = SchnorrSignature::<Ed25519>::read::<&[u8]>(&mut sig_bytes.as_ref()).unwrap();
assert!(sig.verify(public_key, Scalar::from(7u64))); // forged signature accepted
```

Note: I could not enumerate every in-scope caller that feeds an attacker-controlled `public_key` into `verify`/`batch_verify`/half-aggregation; the vulnerability is proven at the library level (the reachable `read`/`verify` APIs), and a caller that gates keys by a membership check (as `verify_transaction` does via `get_and_increment_nonce`) is not affected. The unconditional missing identity check in `SchnorrSignature::read`/`verify` and `Ciphersuite::read_G` is the concrete root cause.