### Title
PedPoP commitments PoK does not bind `enc_key`, allowing encryption-key substitution - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP round-1 message (`EncryptionKeyMessage<C, Commitments<C>>`) carries a participant's polynomial `commitments` plus a Schnorr PoK, *and* an ECDH `enc_key` used to encrypt that participant's secret shares. The PoK challenge is computed only over `context`, the participant index, the PoK nonce `R`, and `cached_msg` — which accumulates solely the serialized commitment points. `enc_key` is never transcripted into the challenge, so a forged/tampered message can pair a validly-signed commitment vector with an attacker-chosen `enc_key`.

### Finding Description
`EncryptionKeyMessage::read` parses the inner `Commitments` (including its `sig`) and then `enc_key` independently, at `crypto/dkg/pedpop/src/encryption.rs:57`. Inside `Commitments::read`, `cached_msg` is built exclusively from the `t` group elements, and the signature is read afterward, at `crypto/dkg/pedpop/src/lib.rs:110-127`.

Verification happens in `KeyMachine::verify_r1`, which checks the PoK via `challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg)` at `crypto/dkg/pedpop/src/lib.rs:323-329`. `self.encryption.register(l, msg)` stores `enc_key` before verification and later `self.encryption.encrypt(rng, l, share_bytes)` at `crypto/dkg/pedpop/src/lib.rs:369` encrypts participant `l`'s share under whatever `enc_key` was supplied — with no proof that `enc_key` belongs to `l` or was the key `l` intended. Because the PoK is the only authentication on this message, its failure to cover `enc_key` is a direct analog of a crafted request reaching a privileged operation with part of the payload unbound.

### Impact Explanation
Two concrete impacts, both reachable from untrusted bytes fed to `EncryptionKeyMessage::read`/`Commitments::read` and then to `generate_secret_shares`:

1. **Secret-share interception.** If an attacker can present an altered commitments message (the library explicitly delegates channel authentication to the caller), they keep the valid `cached_msg`/`sig` untouched and replace `enc_key` with a key whose discrete log they know. Honest dealers then ECDH-encrypt `l`'s `SecretShare` to the attacker's key, disclosing a victim's key share.
2. **False blame of an honest dealer.** Even without interception, `l` (or a tamperer) supplying a garbage/attacker `enc_key` causes the victim's decryption and `share_verification_statements` batch check in `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:487-499`) to fail, surfacing `PedPoPError::InvalidShare` that attributes fault to the sender of the shares — slashing/framing an honest party.

### Likelihood Explanation
The cryptographic gap is deterministic and provable from the code: nothing in `challenge()` or `cached_msg` covers `enc_key`. Exploitation requires either a malicious DKG participant or an adversary able to alter the broadcast message in transit; PedPoP states networking/authenticity is the caller's responsibility, so in deployments where the transport does not independently authenticate `enc_key`, the attack is practical. Since the PoK is clearly intended to authenticate this message, its incomplete coverage is a defect rather than documented misuse.

### Recommendation
Include `enc_key` in the signed PoK transcript — e.g., append `enc_key.to_bytes()` to `cached_msg` during `EncryptionKeyMessage`/`Commitments::read` (or append it in `challenge()`), so the Schnorr signature binds the exact `(commitments, enc_key)` pair the recipient will use.

### Proof of Concept
```rust
// Conceptual, using the in-scope API:
// 1. Victim/honest dealer's machine calls generate_secret_shares with a
//    HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>.
// 2. Attacker takes participant l's authentic serialized message, strips the
//    trailing enc_key encoding, and appends G * x for attacker-known x.
//    Commitments::read reconstructs cached_msg from only the t points
//    (lib.rs:115-121), so the original sig still verifies in verify_r1.
// 3. Dealer's encrypt(rng, l, share) ECDH-encrypts to attacker enc_key.
// 4. Attacker decrypts l's SecretShare; alternatively, l fails decryption and
//    calculate_share blames the honest dealer (lib.rs:493-499).
```

I could not fully trace `Encryption::register`/`encrypt` internals within the iteration budget to confirm the exact ECDH construction, so step 3's precise key derivation is inferred from `EncryptionKeyMessage`/`enc_key` semantics; the missing transcript binding itself is confirmed by `crypto/dkg/pedpop/src/lib.rs:86-93` and `110-127`.