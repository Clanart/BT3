### Title
Published blame proofs reveal the ECDH shared key, leaking a valid DKG secret share to any observer - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the Mattermost report (an endpoint returning data to parties lacking permission), Serai's PedPoP blame flow turns a pairwise-private encrypted secret share into publicly decryptable data. When a participant accuses a sender of an invalid share, they publish an `EncryptionKeyProof` containing `key` — the raw ECDH point `msg.key * enc_key` — plus a DLEq proving it is well-formed. Anyone who observes the blame (e.g., via the coordinator's `Transaction::InvalidDkgShare` handling, which forwards `share` and `blame` to `key_gen::CoordinatorMessage::VerifyBlame`, or any `AdditionalBlameMachine` evaluator) can reconstruct the ChaCha20 keystream with `cipher(context, &proof.key)` and recover the plaintext `SecretShare`, even though `blame_internal` deliberately only *returns* a `Participant` and never discloses the share value. A party that never received that share — i.e., had no permission to it — obtains a valid, verified piece of the distributed private key.

### Finding Description
- `Decryption::decrypt_with_proof` verifies `proof.dleq` against `[G, msg.key]` and `[enc_keys[decryptor], proof.key]`, then decrypts with `cipher(context, &proof.key)` — confirming `proof.key` is exactly the ECDH secret for that message (crypto/dkg/pedpop/src/encryption.rs:366-397).
- `EncryptionKeyProof` is `Clone + write + serialize` and carries `key: Zeroizing<C::G>` in plaintext (encryption.rs:259-296).
- `Encryption::decrypt` hands this proof to the accuser as the `blame` attached to `PedPoPError::InvalidShare` (encryption.rs:469-500; lib.rs:477-499).
- `blame_internal` returns only `sender`/`recipient` (lib.rs:575-609), giving the impression the share stays private, yet the proof published alongside the accusation is sufficient for any third party holding the `EncryptedMessage` (which is "a copy of the encrypted secret share", i.e., broadcast/transcript-visible) to decrypt it themselves: `cipher::<C>(context, &proof.key).apply_keystream(msg)` fully recovers `SecretShare`.
- `AdditionalBlameMachine::new` is explicitly usable "regardless of if the caller was a member in the DKG protocol" (lib.rs:638-662), so the decryption capability is exercisable by unprivileged observers, not just participants.

### Impact Explanation
A secret share that the protocol intends to be visible only to `(sender, recipient)` becomes recoverable by any observer of the blame. If the share is *valid* (the recipient lied, so `recipient` is blamed), the attacker obtains a valid evaluation of a contributor's polynomial. Across repeated/accumulated blame events for the same victim recipient — which `AdditionalBlameMachine` is explicitly designed to process indefinitely — an observer can collect the full set of `t` secret shares destined for one participant and reconstruct that participant's entire private key share, undermining the threshold guarantee. Even single-share disclosure erodes the secrecy of the aggregate key polynomial.

### Likelihood Explanation
Low-to-moderate trigger rate but zero cost to observe: it requires a false accusation (a recipient blaming a sender whose share was actually valid) or an invalid-share incident where the proof is published anyway. The coordinator and `AdditionalBlameMachine` paths make the proof a public transcript artifact; no privileged position is needed to exploit it.

### Recommendation
Do not expose the raw ECDH point. Replace `EncryptionKeyProof` with a *keyed-decryption* ZK proof (e.g., a DLEq proving correctness plus a commitment to the decrypted share, verified against `share_verification_statements` in zero knowledge), or have `blame` publish only a Schnorr-style proof linking the decrypted share to the sender's commitments without a reusable decryption key. At minimum, treat `EncryptionKeyProof.key` as secret: never serialize it into public transactions, and make blame verification require the accused message + proof under a session-bound transcript so the proof cannot be replayed by outsiders to decrypt.

### Proof of Concept
1. Participants run PedPoP; sender `i` sends `EncryptedMessage` `m` to recipient `j` (observable on the authenticated channel).
2. `j` calls `calculate_share`, which via `Encryption::decrypt` produces `blame = EncryptionKeyProof { key: ecdh(enc_key_j, m.key), dleq }` and raises `PedPoPError::InvalidShare { participant: i, blame: Some(blame) }`.
3. `j` publishes the accusation (`Transaction::InvalidDkgShare` / `AdditionalBlameMachine::blame`), attaching `m` and `blame`.
4. An outside observer computes `cipher::<C>(context, &blame.key)` and applies the keystream to `m.msg`, yielding `SecretShare<C::F>` — verified valid by `from_repr` and `share_verification_statements` — without ever possessing `j`'s `enc_key`.