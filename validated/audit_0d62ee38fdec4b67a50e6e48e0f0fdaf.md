### Title
Identity (zero) public key accepted as a verification key, enabling universal signature forgery — ([File: crypto/schnorr/src/lib.rs])

### Summary
The external report describes granting `DEFAULT_ADMIN_ROLE` to the zero address: a privileged role bound to a value that no party should control, accepted because no zero-check exists. The Serai analog is the acceptance of the identity group element (the additive "zero") as a legitimate public key / group key. Neither `SchnorrSignature::verify` nor `ThresholdKeys::new`/`ThresholdKeys::read` rejects an identity key. `C::read_G` (`crypto/ciphersuite/src/lib.rs:91`) performs only canonical-encoding validation, and the identity encoding is canonical, so an attacker-controlled byte stream fed to `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`) can install an identity group key. Any Schnorr signature equation evaluated against that key degenerates and accepts forgeries for arbitrary messages.

### Finding Description
`SchnorrSignature::verify` computes `multiexp_vartime` over `batch_statements`, i.e. it checks `R + c·A − s·G == identity` (`crypto/schnorr/src/lib.rs:88-110`). If `A` is the identity point, the term `c·A` vanishes for every challenge `c`, leaving `R − s·G == 0`. A forger picks any scalar `s`, sets `R = s·G`, and produces a signature `(R, s)` that verifies for **any** message/challenge under the identity key — a universal forgery requiring no secret.

The identity key is reachable through public deserialization paths:

- `C::read_G` only checks `from_bytes` success and canonical round-trip (`crypto/ciphersuite/src/lib.rs:91-101`); the identity point's encoding is canonical, so it is accepted.
- `ThresholdKeys::read` reads `n` verification shares via `read_G` (`crypto/dkg/src/lib.rs:620-623`) and passes them to `ThresholdKeys::new`, which derives `group_key` as `Σ verification_shares[i] · interpolation_factor(i)` for `i = 1..=t` (`crypto/dkg/src/lib.rs:376-378`) with no check that the shares or the resulting `group_key` are non-identity. Setting every verification share to identity yields `group_key == identity`, or shares can be chosen so their Lagrange-weighted sum cancels to identity.
- `Participant::new` and `ThresholdParams::new` reject zero indexes (`crypto/dkg/src/lib.rs:166-176`), and `ThresholdKeys::scale` rejects a zero scalar (`crypto/dkg/src/lib.rs:400-403`), showing the codebase understands zero-value rejection — but the analogous check on group elements was never applied.

Downstream, `ThresholdView`, FROST signing, and any verifier consuming `group_key()` inherit the identity key, so forged `(R, s)` pairs pass `SchnorrSignature::verify`/`batch_verify` for arbitrary challenges.

### Impact Explanation
A forged signature on any message is the acceptance criterion explicitly listed in the rules. Wherever Serai components verify Schnorr/BIP-340-style signatures (or FROST aggregate outputs) against a deserialized `ThresholdKeys` group key, an attacker who can cause a keyset whose group key is the identity — via `ThresholdKeys::read` on untrusted bytes — can produce valid signatures without any secret material. This is the direct analog of "renounced ownership granting admin rights to the zero address": the zero element acquires a privileged authorization position it was never checked against.

### Likelihood Explanation
Requires an attacker to supply crafted serialized `ThresholdKeys` bytes (or otherwise cause an identity group key to be used). The bytes are fully public-input-controlled: `t`, `n`, `i`, interpolation tag, secret share, and `n` identity-encoded verification shares. Feasibility depends on an integrator feeding untrusted bytes into `ThresholdKeys::read` or verifying against a deserialized key, which is within the stated scope (`ThresholdKeys::read` is an enumerated entry point). Medium likelihood.

### Recommendation
- Reject the identity element in `C::read_G` call sites that accept public keys/verification shares, or add an explicit `bool::from(point.is_identity())` rejection in `ThresholdKeys::new` for each verification share and for the computed `group_key`.
- Add an identity check on `public_key` in `SchnorrSignature::verify`/`batch_verify` as defense-in-depth, mirroring the zero-address check recommended in the external report and the existing zero-scalar rejection in `ThresholdKeys::scale`.

### Proof of Concept
```rust
// Conceptual PoC against crypto/schnorr and crypto/dkg
// 1. Craft ThresholdKeys bytes: t=1, n=1, i=1, Lagrange, any secret share,
//    verification_shares[1] = identity encoding (0x00..01 compressed identity).
//    ThresholdKeys::read accepts it; group_key() == identity.
// 2. Forge a signature for any challenge c:
let s = <C as Ciphersuite>::F::random(rng);
let R = C::generator() * s;
let forged = SchnorrSignature::<C> { R, s };
// verify computes R + c*identity - s*G = s*G - s*G = identity -> true
assert!(forged.verify(C::G::identity(), challenge));
```
`batch_statements` at `crypto/schnorr/src/lib.rs:88-100` confirms the `(challenge, public_key)` pair contributes nothing when `public_key` is identity, so the check reduces to `R == s·G`, satisfied by construction.