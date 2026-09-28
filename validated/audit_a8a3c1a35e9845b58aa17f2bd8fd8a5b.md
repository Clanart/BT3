### Title
Identity group key from crafted `ThresholdKeys` yields trivially forgeable Schnorr signatures - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` deserializes verification shares via `Ciphersuite::read_G`, which enforces canonicality but does **not** reject the identity element, and `ThresholdKeys::new` performs no consistency check between the supplied `secret_share`/`verification_shares` and no check that the derived `group_key` is non-identity. An attacker who feeds crafted bytes into `ThresholdKeys::read` can therefore materialize a `ThresholdKeys` whose `group_key()` is the identity point. Under an identity public key, `SchnorrSignature::verify`'s relation `R + c·A − s·G = 0` degenerates to `R = s·G`, so `(R = x·G, s = x)` verifies for any `x` and any challenge — an unrestricted signature forgery independent of the message.

### Finding Description
The deserialization path is:

- `ThresholdKeys::read` reads `n` verification shares with `<C as Ciphersuite>::read_G(reader)` (crypto/dkg/src/lib.rs:620-623). `Ciphersuite::read_G` only checks canonical encoding, never `is_identity` (crypto/ciphersuite/src/lib.rs:91-100). Contrast `Curve::read_G` in FROST, which explicitly rejects identity points (crypto/frost/src/curve/mod.rs:125-131) — so nonce commitments and preprocess points are protected, but key material is not.
- `ThresholdKeys::new` validates only the count of `verification_shares` (`== n`) and that indices are `<= n` (crypto/dkg/src/lib.rs:355-365), then computes `group_key = Σ_{i ∈ 1..=t} verification_shares[i] · λ_i` with no identity check on the result (crypto/dkg/src/lib.rs:376-378). There is no proof or check that `verification_shares[i] == secret_share_i · G` for the holder's own share either.
- `SchnorrSignature::verify` builds the multiexp `[R, cA, -sG]` (crypto/schnorr/src/lib.rs:88-110). With `A = group_key = identity`, the term `c·A` vanishes and the check reduces to `R == s·G`. The attacker picks any scalar `s`, sets `R = s·G`, and the signature verifies for every challenge `c` and every message.

Additionally, because `ThresholdKeys::new` never correlates the holder's `secret_share` with `verification_shares[i]`, a crafted key also makes the holder emit signature shares that fail share-verification (`sG ≠ R_i + c·Y_i`), deterministically drawing blame onto the victim in `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:475-489).

### Impact Explanation
Two concrete consequences, both reachable purely from untrusted bytes fed to `ThresholdKeys::read`:

1. **Signature forgery (spoofing).** Any entity verifying a `SchnorrSignature` against the deserialized key's `group_key()` (which is identity) accepts `(x·G, x)` as a valid signature on any message. This is a direct analog of the "improper handling of missing special element → spoofing" class: the identity element — the special element of the group — is accepted where it semantically represents "no key," collapsing the verification equation.
2. **Self-blame / framing.** The holder's own shares are guaranteed invalid against the attacker-supplied verification shares, causing blame to be attributed to the honest holder rather than to the malformed key material.

### Likelihood Explanation
Exploitation requires the attacker to cause a victim or a verifying component to deserialize attacker-chosen bytes via `ThresholdKeys::read`. That entry point is a public `read` API over a `std::io::Read`, takes no authentication, and performs all validation itself; the only rejection checks present are curve-ID mismatch, invalid participant index, unknown interpolation tag, and non-canonical encodings. No cryptographic binding of the shares to any authority exists in the format, so satisfying the parser is trivial for an unprivileged party.

### Recommendation
- In `ThresholdKeys::read` (and `ThresholdKeys::new`), reject identity verification shares — e.g., route point deserialization through a helper equivalent to `Curve::read_G`'s identity rejection, or check `is_identity` per share and on the computed `group_key`.
- In `ThresholdKeys::new`, verify `verification_shares[&params.i()] == C::generator() * secret_share` so a holder's share is consistent with the advertised share map.
- Defensively, `SchnorrSignature::verify`/`batch_statements` callers that consume externally-supplied public keys should reject `public_key.is_identity()`.

### Proof of Concept
```rust
// C = Ristretto; craft bytes for ThresholdKeys::read with t = n = 2, i = 1.
let mut buf = vec![];
buf.extend(4u32.to_le_bytes());           // C::ID len
buf.extend(C::ID);
buf.extend(2u16.to_le_bytes());           // t
buf.extend(2u16.to_le_bytes());           // n
buf.extend(1u16.to_le_bytes());           // i
buf.push(1);                              // Interpolation::Lagrange
// holder's "secret share" — attacker-chosen, consistency never checked
buf.extend(C::F::ONE.to_repr().as_ref());
// verification_shares: identity for participant 1, identity for participant 2
buf.extend(C::G::identity().to_bytes().as_ref()); // accepted: canonical encoding
buf.extend(C::G::identity().to_bytes().as_ref());

let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();
// t = 2, Lagrange over {1,2}: λ1·id + λ2·id = identity
assert!(bool::from(keys.group_key().is_identity()));

// Forgery: signature on ANY message under keys.group_key() == identity.
// verify checks R + c·A − s·G == 0  =>  R == s·G since A = identity.
let s = C::random_nonzero_F(&mut OsRng);
let forged = SchnorrSignature::<C> { R: C::generator() * s, s };
for c in [challenge_1, challenge_2 /* any Hram output, any message */] {
    assert!(forged.verify(keys.group_key(), c));
}
```

Note on confidence: the cryptographic degeneracy (identity public key ⇒ universal forgeries) and the missing identity/consistency checks in `ThresholdKeys::read`/`ThresholdKeys::new` are directly evidenced in the cited code. The end-to-end reachability — a production path where a verifier accepts signatures against a group key sourced from attacker-deserialized `ThresholdKeys` rather than from in-protocol key generation — is plausible per the accepted input surfaces but was not traced to a concrete processor/coordinator call site within this analysis.