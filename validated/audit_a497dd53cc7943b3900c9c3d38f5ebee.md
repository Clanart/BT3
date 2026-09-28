### Title
Missing recipient binding in PedPoP `EncryptedMessage` PoP lets any participant frame an honest DKG peer by replaying their ciphertext under a foreign encryption key — (`crypto/dkg/pedpop/src/encryption.rs`)

### Summary
Like CVE-2016-0706 (a privileged servlet missing from `RestrictedServlets.properties`, letting authenticated users read data scoped to others), Serai's PedPoP `EncryptedMessage` lacks any binding to its intended recipient. The Schnorr proof-of-possession and the ChaCha20 cipher transcript authenticate the *sender* (`from`) and the ciphertext, but never the recipient's participant ID or encryption key. The blame protocol then decrypts whatever ciphertext an accuser presents under *the accuser's* registered ECDH key. An attacker can therefore take any honestly generated share ciphertext — which is broadcast publicly in `DkgShares` tributary transactions — claim it was addressed to them, produce a valid `EncryptionKeyProof` for their own key, and force the blame machine to decrypt it to garbage, deterministically blaming the innocent sender.

### Finding Description
`encrypt()` builds the PoP as `SchnorrSignature::sign(&key, nonce, pop_challenge::<C>(context, pub_nonce, pub_key, from, msg))` — the challenge covers context, nonce, ephemeral key, sender, and ciphertext, but not the recipient (`crypto/dkg/pedpop/src/encryption.rs:154-166`). `cipher()` similarly derives the ChaCha20 key from `context || shared_key` only (`encryption.rs:101-132`). Nothing in the serialized `EncryptedMessage` (`key || pop || msg`, `EncryptedMessage::read` at `encryption.rs:171-177`) commits to `to`, the `enc_keys[participant]` value used at `encryption.rs:466`.

At blame time, `Decryption::decrypt_with_proof` verifies the PoP against `from` (sender), then verifies the accuser-supplied `EncryptionKeyProof` DLEq as `proof.key == enc_keys[decryptor] * msg.key`, and decrypts with that key (`encryption.rs:374-397`). Since `decryptor` is the accuser (`recipient`), any participant can generate this proof for *any* `msg.key` because they know their own `enc_key`. `blame_internal` then returns `sender` whenever the decrypted bytes aren't a canonical valid share (`crypto/dkg/pedpop/src/lib.rs:590-605`) — which is guaranteed when the ciphertext was never encrypted under the accuser's key.

The reachable path: coordinator `CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame }` reads attacker-supplied `share` bytes via `EncryptedMessage::read` and feeds them to `AdditionalBlameMachine::blame(accuser, accused, ...)` (`processor/src/key_gen.rs:504-556`); a `sender` result yields `ProcessorMessage::Blame { participant: accused }`.

### Impact Explanation
An honest DKG participant is adjudicated faulty for a valid share. In Serai, DKG fault attribution feeds slashing (`InvalidDkgShare` / `Blame` messages drive fatal slashes), so this converts a passive observation of public share ciphertexts into wrongful punishment of a victim validator and abort of the key-generation session. The code's own threat model anticipates the symmetric attack (Eve reusing Alice's ephemeral key to make a blame proof leak Alice's message — mitigated by the PoP, `encryption.rs:84-90`), but not the transpose: replaying a message to a *different* recipient, which the missing recipient binding leaves wide open.

### Likelihood Explanation
The attacker needs (a) a victim's `EncryptedMessage` bytes — published to all validators in `DkgShares` transactions — and (b) their own encryption key, which they hold as a normal participant. They compute `proof.key = enc_key * msg.key` plus the DLEq and submit `VerifyBlame`. No collusion, no key compromise, no timing. The only mitigation is a social/consensus layer ensuring blame messages correspond to actual deliveries — precisely the external "authenticated channel" assumption the library says callers must enforce, but nothing cryptographically prevents replay-to-wrong-recipient.

### Recommendation
Bind the recipient into the ciphertext's authentication: append `to`'s `Participant` ID (or `enc_keys[to]`) to `pop_challenge` in `encrypt()`/`decrypt()`/`decrypt_with_proof`, and/or to the `cipher` transcript. Then a ciphertext replayed under the wrong recipient fails PoP verification, and `blame_internal` attributes fault to the accuser (`DecryptionError::InvalidSignature → sender` path becomes unreachable for replays; wrong-recipient submissions become accuser fault).

### Proof of Concept
1. Honest sender S runs `generate_secret_shares`, producing `msg_SB: EncryptedMessage` for recipient B; it is published in the `DkgShares` transaction.
2. Malicious participant M reads `msg_SB`, computes `proof.key = M.enc_key * msg_SB.key` and `DLEqProof::prove(&mut encryption_key_transcript(ctx), &[G, msg_SB.key], &M.enc_key)` — a valid `EncryptionKeyProof` since it only asserts knowledge consistent with `enc_keys[M]`.
3. M submits `VerifyBlame { accuser: M, accused: S, share: msg_SB.serialize(), blame: proof.serialize() }`.
4. `decrypt_with_proof(S, M, msg_SB, Some(proof))`: PoP verifies (S did sign it), DLEq verifies (M's proof is genuine), decryption under `cipher(context, M.key * msg_SB.key)` yields garbage → `from_repr` fails or share-verification multiexp is non-identity → `blame_internal` returns `S`.
5. `ProcessorMessage::Blame { participant: S }` — honest S is slashed/faulted.