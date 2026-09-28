### Title
Missing identity/possession sanitization on registered validator keys allows universally forgeable Schnorr signatures and degenerate MuSig signing slots - (File: crypto/ciphersuite/src/lib.rs, coordinator/src/tributary/spec.rs)

### Summary
The CloudStack bug class — "attacker registers unsanitized input that is later consumed by a privileged path, turning a registration flaw into command execution" — maps directly onto Serai's key-registration path: `TributarySpec::new` deserializes validator public keys with `<Ristretto as Ciphersuite>::read_G`, which enforces canonical encoding but does **not** reject the identity point. Identity is then stored in `validators` and flows into `musig()` key aggregation and `Signed` transaction verification, where a public key of identity makes the Schnorr verification equation degenerate (`s·G == R`, satisfiable by anyone with no secret). The registration step is the injection; the verification steps are the execution sink.

### Finding Description
`Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) performs canonical checks (re-encode equality) but accepts the identity point. Only `frost::Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`) adds an `is_identity` rejection — and it is **not** used on the registration paths:

- `coordinator/src/tributary/spec.rs:64` — `TributarySpec::new` reads each registered validator key via `<Ristretto as Ciphersuite>::read_G` and stores it in `validators`, where it defines consensus weight via `n()`, `t()`, `i()` (lines 92-139).
- `crypto/dkg/musig/src/lib.rs:46-63` — `check_keys` dedups keys by encoding but never rejects identity, so a registered identity key becomes a `verification_shares` entry and participant slot in `musig()` (lines 149, 155-160).
- `coordinator/tributary/src/transaction.rs:53,87` — `Signed::read`/`read_without_nonce` read `signer` via `Ristretto::read_G` (ciphersuite-level). Identity `signature.R` is rejected (lines 63-69), but identity `signer` is not.
- `crypto/schnorr/src/lib.rs:88-110` — `batch_statements`/`verify` compute `R + c·A - s·G == 0`. With `A = identity`, this reduces to `R == s·G`, satisfiable for arbitrary `s`, `R = s·G` — no private key required.
- PedPoP has the same shape: `EncryptionKeyMessage::read` (`crypto/dkg/pedpop/src/encryption.rs:57-59`) and `Commitments::read` (`crypto/dkg/pedpop/src/lib.rs:115-127`) use `C::read_G`, so a DKG participant can register `enc_key = identity` (forcing every sender's ECDH shared point to the publicly-known identity) or `commitments[0] = identity` (the PoK at `lib.rs:323-329` is then trivially forgeable since the public key is identity).

### Impact Explanation
An account-level party (matching the CVE's PR:L reachability — validator registration is an authenticated-but-low-privilege action, not a colluding-threshold assumption) registers the Ristretto identity encoding as their public key. Consequences:

1. **Signature forgery under a "registered" key**: any `Signed` tributary transaction attributing `signer = identity` verifies for any message — the attacker picks `s` arbitrarily, sets `R = s·G`, and `verify` (schnorr `lib.rs:108-110`) passes because `c·identity` vanishes. The identity check on `signature.R` (`transaction.rs:63`) does nothing because the forgery uses a non-identity `R`.
2. **Degenerate MuSig slot**: for a musig set containing an identity key, that participant's signature share is `d + b·e + c·λ·0 = d + b·e` — anyone can choose nonces `d, e`, publish `D = d·G, E = e·G`, and emit a share that passes `verify_share`, since the verification share is `binding_factor · identity`-equivalent. The slot requires no secret at all, silently removing one party's worth of security from the n-of-n / threshold assumption wherever these keys gate funds (validator-set `set_keys` signatures, `musig_key_vartime` group key in `substrate/validator-sets/primitives/src/lib.rs:136-145`).
3. **DKG confidentiality break**: a PedPoP participant registering `enc_key = identity` causes `ecdh(key, to)` (`encryption.rs:95-97, 466`) to produce the identity point, whose `cipher()` key is derivable by anyone — all secret shares addressed to that participant are world-decryptable.

This is a forged-signature / incorrect-verifier outcome reachable purely from attacker-supplied bytes in a registration message — the exact analog of an unsanitized registered template name reaching command execution.

### Likelihood Explanation
Reachability is high where validator registration accepts arbitrary compressed points: `TributarySpec::new` only requires `read_G` to succeed, and `[0u8; 32]` is the canonical Ristretto identity encoding — a one-byte-class payload, no grinding. Exploitation requires the identity key to be admitted into an active set, which needs a registration transaction; no collusion, no leaked keys, no malicious-peer assumptions beyond one registered (staked) slot — equivalent privilege level to the CVE. Impact is bounded by that slot's weight: a single identity key cannot alone reach `2n/3+1`, so this is Medium rather than Critical — but it unconditionally converts one registered slot into a forgeable signing identity, which the protocol assumes requires an undisclosed discrete log.

### Recommendation
- Reject identity (and document the choice) at the registration boundary: switch `TributarySpec::new` and `Signed::read` to `Curve::read_G`-style checks (`res.is_identity()` → error), or add an explicit identity rejection in `Ciphersuite::read_G` since no in-scope caller legitimately needs identity.
- In `crypto/dkg/musig/src/lib.rs` `check_keys`, reject identity keys alongside duplicates so a degenerate slot cannot enter a MuSig set.
- In PedPoP, reject identity `enc_key` in `Decryption::register` and reject identity in `Commitments::read` (or verify PoK with a non-identity public key requirement) to keep the rogue-key/ECDH invariants.

### Proof of Concept
```rust
// 1) Registration: identity is accepted as a validator key
let identity_enc = [0u8; 32]; // canonical Ristretto identity
let key = <Ristretto as Ciphersuite>::read_G::<&[u8]>(&mut identity_enc.as_ref())
    .unwrap(); // succeeds: identity passes canonical checks in ciphersuite read_G
// TributarySpec::new (coordinator/src/tributary/spec.rs:64) stores it as a validator.

// 2) Forge a Schnorr signature under that key for ANY challenge c:
//    verify checks R + c*A - s*G == 0; with A = identity this is R == s*G.
let s = <Ristretto as Ciphersuite>::F::random(&mut OsRng);
let forged = SchnorrSignature::<Ristretto> { R: Ristretto::generator() * s, s };
assert!(forged.verify(key /* identity */, arbitrary_challenge)); // always true

// 3) Same degenerate slot in MuSig: verification_shares[i] = identity,
//    so that participant's share s_i = d + b*e verifies for attacker-chosen nonces.
```

Exact root cause: `read_G` in `crypto/ciphersuite/src/lib.rs:91-101` admits identity; consumed unsanitized at `coordinator/src/tributary/spec.rs:64`, `coordinator/tributary/src/transaction.rs:53`, `crypto/dkg/musig/src/lib.rs:149`, `crypto/dkg/pedpop/src/encryption.rs:58`, and `crypto/dkg/pedpop/src/lib.rs:115-127`; the degenerate verification is `crypto/schnorr/src/lib.rs:88-110`.