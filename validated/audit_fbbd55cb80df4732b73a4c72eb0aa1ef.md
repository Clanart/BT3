### Title
PedPoP encrypted shares are not bound to their intended recipient, allowing a third party to get an honest DKG participant blamed - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptedMessage`'s proof-of-possession transcript (`pop_challenge`) binds the sender and the ciphertext but never binds the intended recipient or the recipient's encryption key. Because authentication of the message only proves "sender Alice produced these bytes", any party who can relay/copy Alice's `EncryptedMessage<C, SecretShare>` can present it as a share addressed to a different participant. The wrong recipient decrypts under their own ECDH key, obtains an invalid share, and the blame protocol then identifies the honest sender — not the relayer — as faulty.

### Finding Description
In `encrypt()`, each share is encrypted with a per-message key via ECDH between the ephemeral key and the *recipient's* registered encryption key (`self.decryption.enc_keys[&participant]` in `Encryption::encrypt`, encryption.rs:460-467). The `pop` Schnorr signature is computed over `pop_challenge(context, pub_nonce, pub_key, from, msg)` (encryption.rs:302-324), which commits `context`, `nonce`, `key`, `sender`, and the ciphertext — but not the destination participant index nor `to` (the recipient encryption key passed to `ecdh` at encryption.rs:154).

On the receive side, `KeyMachine::calculate_share` decrypts each share with the local party's `enc_key` (encryption.rs:487) and verifies the PoP with `from` = the *claimed sender's* Participant index — which still verifies, since the bytes and sender are genuine. The decrypted bytes will not be a valid share for this recipient, so `share_verification_statements` fails and a `PedPoPError::InvalidShare` with blame is raised (lib.rs:487-499).

During blame resolution, `Decryption::decrypt_with_proof` (encryption.rs:366-397) verifies the accuser's DLEq proof of the ECDH key and checks the decrypted share. Because the message was never bound to the accuser, the decrypted share is invalid, and `blame_internal` returns `sender` (lib.rs:590-605): the honest dealer is adjudicated faulty.

### Impact Explanation
An unprivileged party (anyone able to observe and re-deliver the per-participant share messages, e.g. a relayer or a participant in a different position of the share map) can cause a correctly-behaving DKG dealer to be blamed and — in Serai's deployment where blame carries fatal slashing — penalized, and force abort of the key generation. This is the same class as the Cockpit advisory: a missing authorization/binding check lets an unauthorized party trigger disclosure/punishment paths on data they were not a party to. Additionally, the accuser's blame proof reveals their ECDH shared key for a message never intended for them, satisfying the "blame proofs revealing ECDH keys" concern noted in the scope rules.

### Likelihood Explanation
The attack requires only the ability to present Alice's ciphertext in another participant's share slot — no key knowledge, no collusion, and no invalid cryptography. All primitives verify correctly; the protocol simply never checks that the ciphertext was addressed to the accuser. Any DKG session where messages transit an observable/authenticated-but-public channel is exposed, and a single misrouted share aborts the protocol with an honest party blamed.

### Recommendation
Bind the recipient into the per-message authentication: extend `pop_challenge` to also `append_message(b"recipient", recipient_enc_key.to_bytes())` (or the recipient `Participant` index), threading `to`/`participant` through `encrypt`, `decrypt`, and `decrypt_with_proof` so the PoP proves knowledge of the ephemeral key *and* intent for a specific recipient. Then a replayed message fails PoP verification at `BatchId::Decryption` and blame correctly resolves to the party who injected it (or is simply dropped as inauthentic).

### Proof of Concept
1. Run PedPoP `KeyGenMachine`/`SecretShareMachine` for n ≥ 2; obtain Alice's honest `EncryptedMessage` intended for Bob: `shares_alice[&bob]` from `generate_secret_shares`.
2. Deliver that same serialized message to Carol as her share from Alice (e.g. substitute `shares_to_carol[alice] = shares_alice[bob]`).
3. Carol calls `calculate_share(rng, shares)`; the PoP verifies (sender/context/message all match), but decryption under Carol's `enc_key` yields an invalid scalar/share → `PedPoPError::InvalidShare { participant: alice, blame }`.
4. Carol publishes blame; `BlameMachine::blame`/`AdditionalBlameMachine::blame` calls `decrypt_with_proof(alice, carol, msg, Some(carol_proof))`; the DLEq is valid for Carol's key, the share is invalid → returns `alice`. Honest Alice is blamed; Carol's ECDH key for the misdelivered message is revealed on-chain.