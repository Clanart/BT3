### Title
PedPoP encrypted secret shares are not bound to their recipient, enabling replay of an honest dealer's ciphertext to wrong recipients who then blame-slash the honest dealer - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Airflow bug class is a credential whose scope is broader than intended: the session cookie is sent with `path=/`, so it is presented to any co-hosted application rather than only the Airflow path it was minted for. The Serai analog lives in PedPoP's per-message share encryption: `EncryptedMessage`'s proof-of-possession binds the `context`, the ephemeral `key`, the `nonce`, the `sender`, and the `msg` bytes — but never the **recipient** (nor the recipient's registered encryption key used in the ECDH). A ciphertext validly produced for one recipient is therefore a validly-authenticated "message from sender" for *any* participant in the DKG — the equivalent of a token being accepted outside its intended path.

### Finding Description
In `encrypt()` (crypto/dkg/pedpop/src/encryption.rs:135-168) the message is encrypted with `cipher(context, ecdh(per_message_key, recipient_enc_key))`, so only the intended recipient can decrypt. However, the authenticity proof `pop` is computed over `pop_challenge(context, pub_nonce, pub_key, from, msg)` (crypto/dkg/pedpop/src/encryption.rs:302-324), which omits the recipient entirely. `decrypt_with_proof`/`decrypt` (crypto/dkg/pedpop/src/encryption.rs:374-485) verify the PoP against `from` and then decrypt under the *local* participant's `enc_keys[&decryptor]`, so a relayed copy of Alice's message-to-Bob presented to Carol passes PoP verification as a genuine Alice message but decrypts to garbage under `ecdh(alice_msg_key, carol_enc_key)`.

In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:463-499), a share that fails `C::F::from_repr` or the `share_verification_statements` batch check produces `PedPoPError::InvalidShare { participant: l, blame }`. Carol publishes her `EncryptionKeyProof` (a DLEq over `enc_keys[carol]` — crypto/dkg/pedpop/src/encryption.rs:381-393). In `blame_internal` (crypto/dkg/pedpop/src/lib.rs:575-609), that proof is *valid* (it genuinely is the ECDH key between the message key and Carol's registered key), decryption succeeds, the plaintext fails scalar decode / share verification, and `blame_internal` returns `sender` — i.e., the honest Alice is adjudicated faulty, aborting the protocol and (per the coordinator integration at processor/src/key_gen.rs:504-549) producing a blame outcome against an innocent validator.

### Impact Explanation
An unprivileged DKG participant (or anyone who observes the authenticated channel, which the spec explicitly notes is the only root of trust for these messages) can take a legitimately broadcast/relayed `EncryptedMessage` addressed to participant X and deliver it as the message for participant Y. Because the PoP does not scope the message to its ECDH recipient, Y's decryption machinery and the `AdditionalBlameMachine`/`BlameMachine` blame adjudication both treat it as an authentic sender message whose contents are invalid — deterministically attributing fault to the honest sender. This converts a context-scoping failure into incorrect-blame output, which in Serai's deployment corresponds to slashing/ejecting an honest validator and aborting the DKG.

### Likelihood Explanation
The attack requires only relaying existing public bytes (`EncryptedMessage::read`-able data) to a different `calculate_share`/`blame` call site — no private state, no broken cryptography. It succeeds deterministically whenever the blame path is exercised, because the misdelivery is undetectable: nothing in `pop_challenge` or `Decryption` ties the ciphertext to the recipient identity. The main caveat is that exploitability depends on integration behavior (whether a relayed message can actually be routed to another participant's `calculate_share`), which lives outside the in-scope crates; within the audited crypto code, the missing recipient binding is unconditional.

### Recommendation
Bind the recipient into the per-message authentication: extend `pop_challenge` in crypto/dkg/pedpop/src/encryption.rs to also `append_message(b"recipient", ...)` the recipient's `Participant` index and/or their registered `enc_key`, and pass that value through `encrypt`/`decrypt`/`decrypt_with_proof`. This mirrors the fix pattern of scoping the credential (cookie `path`) to its intended consumer. Alternatively, include `recipient_enc_key` in the cipher transcript so decryption under any other key is provably detectable as misdelivery rather than sender fault.

### Proof of Concept
```rust
// crypto/dkg/pedpop/tests style sketch
// Alice (sender = ONE) encrypts her share for TWO correctly.
let msg_for_two: EncryptedMessage<C, SecretShare<C::F>> =
    alice_encryption.encrypt(rng, TWO, alice_share_for_two);

// Attacker relays the *same bytes* as the message "for" THREE.
// THREE's calculate_share / Decryption::decrypt:
//   1. msg.pop.verify(pop_challenge(context, R, key, ONE, msg)) -> VALID
//      (recipient is not in the challenge)
//   2. cipher(context, ecdh(three_enc_key, msg.key)) -> garbage plaintext
//   3. share_verification fails -> InvalidShare { participant: ONE, blame }

// THREE publishes EncryptionKeyProof proving DLEq(generator, msg.key;
//     three_enc_pub_key, shared_key) -- this proof VERIFIES.

// BlameMachine::blame(ONE, THREE, msg_for_two, Some(proof)):
//   - decrypt_with_proof succeeds (proof is honestly valid)
//   - plaintext is not a canonical scalar / fails share verification
//   - => returns ONE: honest sender Alice is blamed/faulted.
```
The transcript fix that prevents this: `pop_challenge::<C>(context, pub_nonce, pub_key, from, to_enc_key_or_index, msg)` so the PoP verifies only for the intended recipient.