### Title
Tautological torsion check in Ed448 `Point::from_bytes` accepts non-prime-order points through `read_G` - (File: crypto/ed448/src/point.rs)

### Summary
`Point::is_torsion_free` on Ed448 computes `(*self * (Scalar::ZERO - Scalar::ONE)) + self`, which is `-P + P` and therefore always the identity. The torsion check in `GroupEncoding::from_bytes` is a no-op, so `Ciphersuite::read_G` accepts any decodable point — including points outside the prime-order subgroup (Ed448 has cofactor 4). This is the direct analog of a type-confusion: an object of one type (a full-curve point) is treated as another (a prime-order group element) in all downstream math.

### Finding Description
`crypto/ed448/src/point.rs`:

```rust
fn is_torsion_free(&self) -> Choice {
  ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
}
```

`Scalar::ZERO - Scalar::ONE` is `-1 mod l`. So the expression is `(-P) + P = identity` for every `P`, and `is_identity()` always returns true. `GroupEncoding::from_bytes` gates validity on `not_negative_zero & point.is_torsion_free()` (lines 311-317), so the second conjunct provides no filtering.

`Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) then feeds `from_bytes` output through only a canonical re-encoding check (`point.to_bytes() != encoding`), which a canonical encoding of a torsioned point passes. All in-scope consumers that read attacker-controlled points — `Commitments::read` (`crypto/dkg/pedpop/src/lib.rs:110-128`), `EncryptedMessage::read` (`crypto/dkg/pedpop/src/encryption.rs:171-177`), `DLEqProof::read`, `SchnorrSignature::read`, `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-623`), FROST preprocess/share reads — accept torsioned points.

Concrete reachable impact path: `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:376-378`) computes `group_key` by summing `verification_shares[1..=t]` weighted by interpolation factors. A serialized `ThresholdKeys` carrying verification shares with a torsion component produces a `group_key` with a nonzero torsion component. The secret share is a prime-field scalar; no combination of prime-order signer shares can ever equal this group key. The threshold group reports a key/address for which no valid signature can be produced — funds sent to it are reported received but are unspendable.

### Impact Explanation
An unprivileged party who supplies bytes to `ThresholdKeys::read`, `Commitments::read`, or any `read_G` consumer on the Ed448 ciphersuite can inject order-4 torsion components into verification shares / commitments. The resulting group key carries a torsion term that honest signers' prime-order shares can never satisfy, yielding an unspendable group key ("funds reported received that are not spendable"). Additionally, any DLEq/PoK verification assumed to hold over the prime subgroup is weakened to the full curve, enabling small-subgroup inconsistencies between co-verified points.

### Likelihood Explanation
Reachable whenever an Ed448-based `ThresholdKeys` blob, PedPoP `Commitments`, or other `read_G` input is supplied by an untrusted party — explicitly in scope per `ThresholdKeys::read`/`Commitments::read`. The torsion component survives all canonical-encoding checks because the encoding is canonical for the full curve. Exploitation requires only a crafted point encoding (computable offline). Mitigating factor: in interactive PedPoP, a participant whose commitments carry unknown-discrete-log torsion fails `share_verification_statements` and is blamed, so the strongest practical impact is via `ThresholdKeys::read` / key-import paths and any protocol that verifies proof statements mixing attacker points.

### Recommendation
Replace the tautological check with an actual cofactor/subgroup check, e.g. verify `self * Scalar::from(4)`-fold clearing lands in the prime-order subgroup — equivalently check `P * l == identity` where `l` is the prime order, or multiply by the cofactor and require the result is in the intended subgroup. Also reject the identity in `read_G` consumers where a nonzero point is required (FROST nonce commitments, encryption keys).

### Proof of Concept
```rust
// crypto/ed448 — conceptual PoC
use ciphersuite::{Ciphersuite, group::GroupEncoding};
use ciphersuite_ed448::Ed448;

// Take any prime-order point P and add an order-4 torsion point T.
let torsioned: Ed448Point = prime_order_point + order_4_point;
let enc = torsioned.to_bytes(); // canonical encoding of the torsioned point

// Passes: from_bytes succeeds because is_torsion_free() is always true,
// and to_bytes round-trips so the canonical check in read_G passes.
let p = Ed448::read_G(&mut enc.as_ref()).unwrap();
// p has a nonzero torsion component yet is accepted as a prime-order group element.

// In ThresholdKeys::read (crypto/dkg/src/lib.rs:620-631), using such points as
// verification_shares yields a group_key with torsion that no signer share set
// can satisfy -> unspendable key.
```