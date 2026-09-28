### Title
PedPoP blame path embeds the ECDH share-decryption key inside a `Debug`/`Display`-able error, causing plaintext logging of the key protecting a FROST secret share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Pterodactyl advisory is a bug class where a secret is placed into a field/channel presumed non-sensitive (a query parameter), so routine infrastructure logging persists the secret in plaintext. The analog exists in `crypto/dkg/pedpop`: `PedPoPError::InvalidShare` carries an `EncryptionKeyProof` whose `key` field is the ECDH shared point — i.e., the decryption key for the FROST secret-share ciphertext — and both the error enum and the proof derive `Debug`. Any caller that logs the returned error (the standard pattern for `thiserror` errors, and the only thing an integrator can do with a `PedPoPError`) writes the share-decryption key to logs in plaintext. Combined with the corresponding `EncryptedMessage` (which travels over the transport and may equally be logged/observed), the key yields the plaintext DKG share.

### Finding Description
In `KeyMachine::calculate_share`, every inbound share is decrypted and a blame artifact is produced unconditionally via `Encryption::decrypt`, which returns `EncryptionKeyProof { key, dleq }` where `key = ecdh(self.enc_key, msg.key)` — the ECDH shared key that derives the ChaCha20 key decrypting that share's ciphertext (`crypto/dkg/pedpop/src/encryption.rs` lines 487–500, 101–133).

When the share fails to deserialize or fails verification, the error returned is `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` (`crypto/dkg/pedpop/src/lib.rs` lines 480–482, 493–499). That proof is:

- embedded in a `#[derive(Debug, thiserror::Error)]` error enum (`lib.rs` lines 37–47) whose format string interpolates `blame` directly, and
- itself `#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]` (`encryption.rs` line 260), so `key: Zeroizing<C::G>` is printed by both `{:?}` and `{}` error formatting.

The `key` field is not redacted (contrast `SecretShare`, which has a hand-written non-exhaustive `Debug`, `lib.rs` lines 242–246, and `Encryption`/`KeyMachine`/`BlameMachine`, which carefully omit secret fields from `Debug`, `lib.rs` lines 285–295, 395–404). The blame proof is designed to be disclosed to arbiters (`AdditionalBlameMachine::new`/`blame`, `lib.rs` lines 649–682), so it is expected to traverse error-reporting and dispute channels — exactly the "safe-looking channel that gets logged" pattern of the advisory.

### Impact Explanation
A participant who triggers an `InvalidShare` error (e.g., by sending a share ciphertext whose plaintext is a non-canonical scalar, via `C::F::from_repr` failing at `lib.rs` line 480, or a bad share failing `share_verification_statements` at lines 430–449/493–499) causes the victim to emit an error value containing the ECDH key for that ciphertext. Anyone who obtains that logged/relayed proof (an unprivileged observer of error logs, metrics pipelines, or blame arbitration messages — including via `AdditionalBlameMachine`, which explicitly supports non-participants, `lib.rs` lines 639–662) can run `cipher::<C>(context, &proof.key)` and recover the plaintext `SecretShare`. Repeating across `t` senders to the same victim yields enough shares to reconstruct that victim's FROST secret share; shares are also directly usable in verification-share equations. This is key-share recovery from data treated as non-sensitive, matching the advisory's shape (secret persisted/relayed through a channel assumed benign).

### Likelihood Explanation
Reachability is high: the malicious party only needs to send the victim a malformed `EncryptedMessage` during `generate_secret_shares`/`calculate_share`, which the in-scope `EncryptedMessage::read`/`calculate_share` API accepts from untrusted bytes. The leak then occurs through the victim's ordinary error handling — no collusion, no broken BFT, no leaked keys required. The proof is created for every decrypted message regardless (`blames.insert(l, blame)` at `lib.rs` line 486), so even the batch-blame path at lines 493–499 surfaces it. The main mitigating factor is that the victim's caller must actually log or forward the error/blame, but that is the intended use of the `blame` field and mirrors the advisory's reliance on standard webserver logging. Severity is Medium: recovery requires the victim's logs/relayed blame to be observable and enough senders to matter, analogous to the advisory's requirement of separately obtaining logs and the account identifier.

### Recommendation
- Redact `EncryptionKeyProof::key` from `Debug` (hand-written non-exhaustive impl like `SecretShare`'s) and from the `PedPoPError::InvalidShare` `Display` string (print `blame: present/absent`, not the proof).
- Treat `EncryptionKeyProof` as secret material at the type level: document that it must only be disclosed to blame arbiters over authenticated channels, never logged.
- Consider making `PedPoPError::InvalidShare` carry only `participant`, with the proof obtainable via a separate explicit accessor so error logging cannot leak it.

### Proof of Concept
1. Honest victim runs `KeyGenMachine::generate_coefficients` → `generate_secret_shares` → `calculate_share` (`crypto/dkg/pedpop/src/lib.rs` lines 156–202, 347–380, 463–499).
2. Attacker (a DKG participant) submits an `EncryptedMessage` whose ciphertext decrypts to a non-canonical scalar (all-`0xff` repr, mirroring the `invalidate_share_serialization` test helper at `encryption.rs` lines 218–239).
3. `calculate_share` decrypts, builds `EncryptionKeyProof { key: ecdh(victim_enc_key, msg.key), dleq }` at `encryption.rs` lines 487–500, then hits `from_repr` → `None` and returns `Err(PedPoPError::InvalidShare { participant: attacker, blame: Some(proof) })` (`lib.rs` lines 480–482).
4. The victim's caller logs the error (`{:?}`/`{}`), persisting `proof.key` — the ChaCha20 key source — in plaintext logs.
5. Log reader takes `proof.key` + the observed ciphertext `msg.msg`, computes `cipher(context, &proof.key).apply_keystream(msg)` per `encryption.rs` lines 101–133, recovering the attacker-generated share plaintext; collecting the analogous blame data for `t` senders yields the victim's full secret share (since `self.secret += share` at `lib.rs` line 484 accumulates exactly those decrypted values).

Caveat: I could not fully verify whether `EncryptionKeyProof` implements `Display` (required by the `#[error("... blame {blame}")]` format) — the `Debug`-derive leak is confirmed regardless, since `{:?}` formatting and derived-`Debug` wrappers print `key` unredacted.