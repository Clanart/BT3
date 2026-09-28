### Title
Missing recipient binding in PedPoP encrypted-share PoP lets a low-privilege participant frame an honest sender via the blame protocol - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Open WebUI advisory is a broken-access-control flaw: a valve readable by any authenticated "Member" discloses data restricted to a specific privileged role. The Serai analog is in PedPoP's per-message proof-of-possession: `pop_challenge` binds the sender and the ciphertext, but never binds the intended recipient (the destination encryption key). Consequently, an encrypted share addressed to participant Bob is accepted as a valid message "to" any other participant Carol. Through the blame protocol (`BlameMachine::blame` / `AdditionalBlameMachine::blame` → `decrypt_with_proof`), Carol can submit Alice's ciphertext-to-Bob as Alice's share-to-Carol, attach a legitimately constructed `EncryptionKeyProof` for her own key, and cause the blame machine to deterministically fault the honest sender.

### Finding Description
`pop_challenge` (crypto/dkg/pedpop/src/encryption.rs:302-324) transcripts `context`, `nonce`, `key`, `sender`, and `msg` — but not the recipient or the recipient's encryption key `enc_keys[to]`. Verification in `Decryption::decrypt_with_proof` (lines 366-397) checks `msg.pop` against this challenge and then verifies the accuser-supplied DLEq against `self.enc_keys[&decryptor]`, where `decryptor` is a caller-chosen `recipient` parameter. Nothing ties `msg` to `decryptor`.

Blame evaluation in `blame_internal` (crypto/dkg/pedpop/src/lib.rs:575-609) then:

1. Verifies the PoP — passes, because the PoP is genuinely from `sender` (Alice).
2. Verifies the accuser's `EncryptionKeyProof` — passes, because Carol knows her own `enc_key` discrete log and computes `proof.key = ecdh(carol_priv, msg.key)` plus a valid `DLEqProof` over `[G, msg.key] → [enc_key_carol, proof.key]` (encryption.rs:381-392).
3. Decrypts the ciphertext under Carol's ECDH key — produces garbage, since the ciphertext was encrypted to Bob's key (ECDH over Bob's `enc_key`, encryption.rs:154).
4. `C::F::from_repr` fails → `blame_internal` returns `sender` (lib.rs:590-593), blaming Alice.

`AdditionalBlameMachine::new` (lib.rs:649-662) requires only the public commitment messages, so anyone evaluating blame — including the coordinator's `VerifyBlame` handler (processor/src/key_gen.rs:504-563) — reaches this code path with attacker-supplied `sender`/`recipient`/`msg`/`proof`. The required ciphertext is obtainable when encrypted shares are relayed/broadcast to the validator set (the blame flow itself publishes the accused `share`, confirming these messages are not confidential to the recipient).

The code's own comments acknowledge this threat model for a *different* key-reuse variant: the PoP exists precisely because an attacker reusing keys could trick a recipient into revealing a blame ECDH key that decrypts an unrelated victim message (encryption.rs:84-90). The recipient-binding gap is the symmetric omission: the message key is bound to the sender but not to whom it was encrypted for.

### Impact Explanation
An honest DKG participant is provably blamed as faulty: `BlameMachine`/`AdditionalBlameMachine` returns `sender`, and in the processor this becomes `ProcessorMessage::Blame { participant: accused }` — a fatal slashing/fault attribution against an innocent validator, plus abort of the key-generation session. This is a forged-blame outcome reachable entirely with public protocol inputs (a published `EncryptedMessage`, the accuser's own encryption key, and a self-generated DLEq proof).

### Likelihood Explanation
Requires: (a) the attacker is a registered DKG participant (or any party able to submit `VerifyBlame` with a registered `recipient`), (b) visibility of one `EncryptedMessage` from the victim sender to another participant. No secrets, collusion, or BFT break is needed; the DLEq proof is trivially generatable from the accuser's own encryption key. Medium likelihood, gated mainly on ciphertext observability in the deployment's message routing.

### Recommendation
Bind the intended recipient into the message authentication. In `encrypt`/`pop_challenge` and both verification sites (`Encryption::decrypt` at encryption.rs:479-485 and `Decryption::decrypt_with_proof` at 374-379), append the recipient participant index and/or `enc_keys[to]` (the `to: C::G` argument already passed to `encrypt` at line 139) to the PoP transcript, so a ciphertext valid for Bob cannot be presented as Carol's. `decrypt_with_proof` should then derive `decryptor` from the bound transcript rather than accepting it as a free parameter.

### Proof of Concept
1. Alice (`sender`) runs PedPoP round 2 and broadcasts `EncryptedMessage` shares; Carol observes Alice's share to Bob: `(key = k·G, pop, msg = Enc_{ecdh(k, enc_key_bob)}(share))`.
2. Carol submits a blame/accusation with `sender = Alice`, `recipient = Carol`, `msg =` Alice's ciphertext to Bob, and `proof = Some(EncryptionKeyProof { key: carol_priv·k·G, dleq: DLEqProof::prove(..., [G, msg.key], carol_priv) })`.
3. `blame_internal`: PoP verifies (sender Alice is correctly bound); DLEq verifies against `enc_keys[Carol]`; decryption under `ecdh(carol_priv, msg.key)` yields non-scalar bytes → `from_repr` fails → returns `sender` = Alice.
4. Honest Alice is reported faulty (`ProcessorMessage::Blame { participant: Alice }`) despite never misbehaving.