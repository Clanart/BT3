### Title
PedPoP encrypted secret shares are not bound to their intended recipient, enabling replay framing and false blame of honest participants - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the Jenkins report (a missing authorization/context check on an action callable with attacker-supplied input), the PedPoP `EncryptedMessage` encryption path performs no check binding a ciphertext to its intended recipient. `encrypt()` ECDHs to the recipient's registered key (`encryption.rs:460-467`) and the per-message proof-of-possession transcript binds only `context`, `nonce`, `key`, `sender`, and `message` (`pop_challenge`, `encryption.rs:302-324`) — never the recipient's `Participant` index or registered encryption key. Because blame adjudication (`Decryption::decrypt_with_proof`, `encryption.rs:366-397`) decrypts with `enc_keys[decryptor]` supplied by the *accuser's* registered key, any party able to relay a published `EncryptedMessage` can present Alice's valid message to Bob as "Alice's share to Carol", and blame resolution will fault honest Alice.

### Finding Description
- `encrypt()` produces `EncryptedMessage { key, pop, msg }` with no recipient field; `EncryptedMessage::read`/`write` (`encryption.rs:171-183`) serialize only those three fields.
- `pop_challenge` (`encryption.rs:302-324`) binds `sender` but not `to`. The comment at `encryption.rs:300-301` states "the per-message keys have no root of trust" — the only identity bound is the sender.
- `decrypt_with_proof` (`encryption.rs:366-397`) verifies the PoP under `from`, then accepts a DLEq proof that `proof.key == enc_keys[decryptor] * msg.key`. The accuser/recipient chooses which `decryptor` key is used; nothing ties the ciphertext to that key.
- `blame_internal` (`lib.rs:575-609`) maps: valid PoP + decryption to non-canonical or invalid share ⇒ `return sender`. Since the replayed ciphertext was encrypted under a different recipient's key, decryption under Carol's key yields garbage → honest `sender` is returned as faulty.

### Impact Explanation
Any participant (or any observer able to submit `VerifyBlame` inputs) can take an `EncryptedMessage` that an honest participant published for someone else — these are necessarily public/relayed so third parties can evaluate blame via `AdditionalBlameMachine::new` (`lib.rs:649-662`) — and accuse the honest sender. The honest sender is declared faulty (`lib.rs:608` returns `sender`), which in deployment leads to a fatal slash of an innocent validator. Impact: integrity/availability loss (unjustified slashing and DKG abort), with attacker cost of a single DLEq proof. Severity: Medium.

### Likelihood Explanation
Requires only a malicious DKG participant/observer plus access to the victim's published `EncryptedMessage` for another recipient — both assumptions match PedPoP's own threat model (the library exists to adjudicate malicious behavior). The accuser needs their own `enc_key` secret to satisfy the DLEq, which they trivially possess.

### Recommendation
Bind the recipient into both the ciphertext and the PoP: extend `pop_challenge` with `transcript.append_message(b"recipient", to_index_or_enc_key)`, include the recipient index in `EncryptedMessage`, and have `decrypt`/`decrypt_with_proof` verify the PoP against the claimed recipient. Reject blame inputs whose embedded recipient does not match the accuser.

### Proof of Concept
1. Alice runs `generate_secret_shares`, publishing `Enc_A→B` (valid share for Bob) and `Enc_A→C` (valid share for Carol).
2. Carol constructs `proof.key = msg.key * enc_key_Carol` and a valid `DLEqProof` over `(g, msg.key) → (enc_keys[Carol], proof.key)` since she knows `enc_key_Carol`.
3. Carol calls `blame(sender=Alice, recipient=Carol, msg=Enc_A→B, proof)`.
4. `decrypt_with_proof`: `pop.verify` passes (PoP never references Bob); DLEq verifies; ChaCha20 keystream under the wrong shared point yields a non-canonical or invalid share.
5. `blame_internal` returns `Alice` — honest Alice is declared faulty and slashed.