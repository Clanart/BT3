### Title
PedPoP encryption keys are registered without a proof of possession or validity check, allowing a malicious participant to register the identity point and frame honest senders in the blame protocol - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG encrypts each secret share with a per-message ephemeral key that is ECDH'd against the recipient's registered long-term encryption key (`enc_key`). `EncryptionKeyMessage::read` and `Decryption::register` accept any `C::G` for `enc_key` — no proof of possession, no identity/torsion rejection, no consistency check is ever performed. A malicious participant can register `enc_key = identity`, which collapses every ECDH shared secret directed at them to the identity point, lets them produce a valid `EncryptionKeyProof` for any ciphertext (DLEq with scalar `0`), and thereby falsely "prove" an honest sender transmitted an invalid share.

### Finding Description
The bug class of the external report is insufficient policy enforcement allowing a bypass of an isolation boundary. The analog here is the missing admission policy on the per-participant encryption key that isolates each recipient's share channel:

- `EncryptionKeyMessage::read` parses `enc_key` with a bare `C::read_G` and `Decryption::register` inserts it unconditionally (lines 57-59, 351-361 of `crypto/dkg/pedpop/src/encryption.rs`). Unlike the per-message `pop` Schnorr signature (lines 84-91, 302-324), which exists precisely to stop key-substitution abuse of the blame mechanism, the long-term `enc_key` itself carries no PoP.
- `encrypt` computes the shared key as `ecdh(key, to)` where `to` is the attacker's registered `enc_key` (line 154, 466). With `enc_key = O` (identity), `k * O = O`, so `cipher(context, O)` produces a publicly computable ChaCha20 key and the "decrypted" share is garbage to everyone including the attacker.
- In `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs`, lines 477-499), the attacker's garbage share fails `share_verification_statements`, producing `PedPoPError::InvalidShare` against the honest sender.
- Crucially, `blame_internal` (lines 575-609) resolves the accusation via `decrypt_with_proof` (lines 366-397): the accuser supplies `EncryptionKeyProof { key, dleq }` verified against `self.enc_keys[&decryptor]` — which is the attacker's registered `O`. A DLEq of scalar `0` between `G → O` and `msg.key → O` verifies cleanly, `cipher(context, O)` decrypts to the same garbage, `from_repr` fails or the share is invalid, and `blame_internal` returns `sender`. The honest sender is adjudicated faulty with a cryptographically valid blame proof.

Any participant can also copy another participant's broadcast `enc_key` or any group element; registration asserts only `!enc_keys.contains_key(&participant)`, never key validity.

### Impact Explanation
An unprivileged DKG participant with only public inputs (their own `EncryptionKeyMessage`, fed through `EncryptedMessage::read`/`Commitments::read` paths to `generate_secret_shares`/`calculate_share`) can:

1. Cause the DKG to abort with an honest participant recorded as the faulty party — defeating the accountability/isolation purpose of the blame subsystem.
2. In a deployment where blame reports feed validator-set management or slashing, get an honest validator falsely blamed/removed while the actual attacker appears honest.
3. Force repeated DKG failures (each retry with fresh attacker keys can frame a different honest member), which for a threshold custody system is a persistent liveness and integrity failure.

The false blame path is the key impact: it is not merely a DoS — it produces an incorrect adjudication signed off by `AdditionalBlameMachine::blame`/`BlameMachine::blame`, which is explicitly designed to be evaluated by third parties (`AdditionalBlameMachine::new` is callable by non-participants, lines 649-662).

### Likelihood Explanation
The attacker needs only to participate in one PedPoP session and submit a crafted `EncryptionKeyMessage` with `enc_key = identity` (or, where `read_G` rejects identity, a small-subgroup/invalid point on non-prime-order groups — the code performs no subgroup/torsion check at registration either). Everything after that is deterministic: honest participants will encrypt to the malicious key, fail verification, and the attacker's scalar-0 DLEq proof will pass `decrypt_with_proof`. One caveat affecting reachability: whether `C::read_G` accepts the identity point depends on the ciphersuite implementation; on Ristretto255 (Serai's primary DKG curve) `CompressedRistretto::decompress` does accept the identity encoding, so no additional bypass is needed there.

### Recommendation
Bind and validate the encryption key at registration, mirroring the existing `pop` mechanism:

- Require a Schnorr proof of possession over `enc_key` inside `EncryptionKeyMessage`, transcript-bound to `context` and the participant index (reuse `pop_challenge`-style binding in `crypto/dkg/pedpop/src/encryption.rs`).
- Reject identity (and, on non-prime-order groups, non-prime-subgroup) `enc_key` values in `Decryption::register`/`EncryptionKeyMessage::read`.
- Optionally include `enc_key` in the round-1 PoK `challenge` (`crypto/dkg/pedpop/src/lib.rs`, line 328) so the commitments message itself authenticates it.

### Proof of Concept
1. Participant `j` (malicious) runs `KeyGenMachine::generate_coefficients`, but replaces the `Encryption` registration payload so its broadcast `EncryptionKeyMessage` has `enc_key = C::G::identity()`. `Commitments::read`/`EncryptionKeyMessage::read` and `register` accept it since no PoP/identity check exists.
2. Every honest participant `s` calls `generate_secret_shares` → `encrypt(rng, j, share)` computes `ecdh(k, O) = O`; ciphertext encrypts under `cipher(context, O)`.
3. `j` runs `calculate_share`; decryption yields garbage for all senders → `PedPoPError::InvalidShare { participant: s, blame }` blaming honest `s`.
4. When `BlameMachine::blame(s, j, msg, proof)` is evaluated (by any party, via `AdditionalBlameMachine`), `j` supplies `EncryptionKeyProof { key: O, dleq }` where `dleq` is `DLEqProof::prove(rng, encryption_key_transcript(context), &[G, msg.key], &0)` — i.e., witness scalar `0`, proving `G*0 = O = enc_keys[j]` and `msg.key*0 = O = proof.key`. This verifies, `cipher(context, O)` decrypts to garbage, `from_repr` or share verification fails, and `blame_internal` returns `s` — the honest sender is publicly adjudicated faulty.