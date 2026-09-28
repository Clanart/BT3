### Title
`ThresholdKeys::read` deserializes attacker-controlled key material without establishing the share/verification-share consistency invariant, letting a malicious blob define a group key the attacker fully controls - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys` assumes — everywhere it is used — that `secret_share * G == verification_shares[i]` and that `group_key` is an honestly shared threshold key. `ThresholdKeys::new` computes `group_key` purely by interpolating the supplied `verification_shares` and never checks the secret share against `verification_shares[i]`, and `Ciphersuite::read_G` (used by `ThresholdKeys::read`) accepts the identity point. Analogous to the "uninitialized/incorrectly-initialized state" bug class: the deserialization path constructs an object whose security-critical invariant was never established in the context it will be used, and every downstream consumer (FROST signing, `recover_key`, wallet `tweak_keys`) silently trusts it.

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`) parses `t, n, i`, the interpolation variant, `secret_share`, and `n` verification shares entirely from the input bytes, then calls `ThresholdKeys::new`. `new` (`crypto/dkg/src/lib.rs:376-390`) computes `group_key = Σ_{j=1..=t} V_j * λ_j` and returns without verifying `C::generator() * secret_share == verification_shares[&i]`. The only cross-check that `secret_share` matches its verification share anywhere in the pipeline is a `debug_assert_eq!` in the dealer (`crypto/dkg/dealer/src/lib.rs:64`), which does not exist in release builds and only runs on honestly generated keys.

Additionally, `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) only enforces canonicality — it does not reject the identity point (that check exists only on `frost::Curve::read_G`, `crypto/frost/src/curve/mod.rs:125-131`, which `ThresholdKeys::read` does not use).

An unprivileged party who can cause a victim to deserialize supplied `ThresholdKeys` bytes (the format explicitly listed as an untrusted input sink) can therefore craft a blob where every verification share `V_j = a_j * G` for attacker-known `a_j`. The resulting `group_key` has discrete log `Σ λ_j a_j` known to the attacker, while the victim sees a well-formed `ThresholdKeys` and reports `group_key()` as the multisig address.

### Impact Explanation
- Funds sent to the derived `group_key` (e.g., after `networks/bitcoin/src/wallet/mod.rs:46` `tweak_keys` or TapTweak on top of it) are spendable solely by the attacker, since they know the group key's discrete log — funds "received" by the threshold are not honestly spendable.
- The attacker can produce valid FROST-equivalent signatures for that group key unilaterally; any signature policy the victim believes is enforced by `t-of-n` is void.
- A weaker variant (mismatched `secret_share` vs `verification_shares[i]`, or identity verification shares for other participants) produces shares that only fail during `verify_share`/blame attribution, corrupting signing sessions in ways blamed on innocent participants.

### Likelihood Explanation
Requires an attacker to supply or tamper with serialized `ThresholdKeys` — reachable wherever keys are provisioned, migrated, backed up, or loaded from a coordinator/dealer rather than generated locally. No collusion, validator compromise, or protocol exploitation needed; it is pure input-validation failure on an explicitly untrusted deserialization surface. The inconsistency is undetectable to the victim short of re-running a DKG check the library never performs.

### Recommendation
In `ThresholdKeys::new` (or at least in `ThresholdKeys::read`), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject identity verification shares. Consider also rejecting `Interpolation::Constant` vectors with zero/duplicate coefficients where inappropriate.

### Proof of Concept
```rust
// Construct ThresholdKeys bytes for which the attacker knows the group key's
// discrete log, with no honest DKG ever occurring.
let t = 2u16; let n = 3u16; let i = Participant::new(1).unwrap();
let a1, a2, a3 = random known scalars;           // attacker-known "shares"
let V = |a| C::generator() * a;
// Lagrange factors for participants {1,2} evaluated at 0: λ_1 = 2, λ_2 = -1 (mod q)
// group_key = 2*V(a1) - V(a2); attacker sets a1=a2=a → group_key = a*G, known.

let mut blob = vec![];
blob.extend((C::ID.len() as u32).to_le_bytes());
blob.extend(C::ID);
blob.extend(t.to_le_bytes()); blob.extend(n.to_le_bytes()); blob.extend(i.to_bytes());
blob.push(1);                                   // Interpolation::Lagrange
blob.extend(a.to_repr());                       // secret_share = a
for share in [V(a), V(a), V(a)] {               // all verification shares = a*G
  blob.extend(share.to_bytes());
}

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap(); // succeeds
assert_eq!(keys.group_key(), C::generator() * a); // attacker knows `a`
// Victim now uses keys.group_key() as the multisig address; attacker spends alone.
```