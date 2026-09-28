### Title
Unverified-PoP blame proof leaks the ECDH key for a co-opted message key, exposing honest shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` returns a blame-able `EncryptionKeyProof` for a share that fails `from_repr` **before** the batched Schnorr proof-of-possession on that message's per-message key has been verified. An unprivileged party who observes a genuine `EncryptedMessage` (whose ephemeral key `key` is a public point) can inject a forged share message reusing that `key` with a garbage ciphertext/PoP. The victim emits a blame proof revealing `enc_key * msg.key` — the ECDH shared key of the *honest* observed message — allowing anyone to decrypt the real secret share. This is precisely the attack the code comments say the PoP exists to prevent; the early error path bypasses it. This mirrors the Salt bug class: improperly handling injected (unauthenticated) messages in the data stream.

### Finding Description
In `Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-501`), the PoP signature is only **queued** into the `BatchVerifier` (line 479), while decryption happens immediately and the `EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq }` is returned unconditionally.

In `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-499`), the loop:
1. calls `decrypt` (PoP queued, not verified),
2. immediately calls `C::F::from_repr(share_bytes.0)` and on failure returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` at lines 480-482 — **before** `batch.verify_with_vartime_blame()` at line 493 ever runs.

The comments at `encryption.rs:83-90` explicitly describe this attack: "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X... Bob would then use this to create a blame argument... they'd reveal bX, revealing Alice's message to Bob." The PoP was added to stop it — but the PoP is never verified before the blame proof (which reveals `bX`) is generated on this error path.

The blame is publicly verifiable: `decrypt_with_proof` (`encryption.rs:381-392`) confirms via the DLEq that `proof.key` is a correct ECDH between Bob's registered `enc_key` and `msg.key` — so the published proof genuinely reveals `bX`, which anyone can feed to `cipher()` to decrypt Alice's original ciphertext.

### Impact Explanation
An unprivileged network-position attacker (or any party able to deliver a `share` message slot for participant `l`, matching the "inserting packets into the minion-master data stream" class) causes the victim to publish a decryption key that reveals an honest participant's FROST/PedPoP secret share. Collecting `t` shares of the DKG output recovers the threshold private key; fewer still reduce security. Additionally, `blame_internal` attributes `InvalidSignature`/`InvalidProof` faults to the *sender*, so the framed honest participant (Alice) is blamed while her share is leaked. Severity: High (key share disclosure + incorrect blame of an honest party).

### Likelihood Explanation
The attacker needs only: (a) a copy of Alice's genuine `EncryptedMessage` (publicly transmitted ciphertext containing `key`), and (b) the ability to submit a share message under Alice's `Participant` index with `key = X`, arbitrary PoP bytes (or a self-signed PoP — possession of `x` isn't needed since it fails anyway), and a ciphertext that won't deserialize to a canonical scalar (e.g., random 32 bytes fails `from_repr` with overwhelming probability). No private keys, no participation in the DKG, and no valid proofs are required. The deterministic early-return makes this reliable.

### Recommendation
In `calculate_share`, do not attach the `EncryptionKeyProof` to an error until the queued PoP for that message has been verified. Concretely: accumulate the `(l, blame, parse_result)` tuples, run `batch.verify_with_vartime_blame()` first (mapping `BatchId::Decryption(l)` failures to `blame: None` as already done), and only then surface `InvalidShare { blame: Some(..) }` for messages whose PoP verified but whose plaintext failed `from_repr`. Alternatively, make `Encryption::decrypt` verify the PoP locally (non-batched) before producing the `EncryptionKeyProof`.

### Proof of Concept
1. Alice runs `generate_secret_shares`, producing `Enc_AB = EncryptedMessage { key: X = xG, pop, msg }` to Bob, encrypting her secret share `s_AB`. `X` is a public field of the wire message (`EncryptedMessage::read`/`write`, `encryption.rs:171-183`).
2. Eve constructs `forged: EncryptedMessage { key: X, pop: <arbitrary SchnorrSignature>, msg: SecretShare(<32 random bytes>) }` and delivers it to Bob in the `shares` map under `Participant = Alice` (`calculate_share`, `lib.rs:466`).
3. Bob's `encryption.decrypt` queues the (invalid) PoP under `BatchId::Decryption(Alice)`, computes `key = ecdh(bob_enc_key, X) = bX`, decrypts to garbage, and returns `(garbage, EncryptionKeyProof { key: bX, dleq: valid })`.
4. `C::F::from_repr(garbage)` fails → `PedPoPError::InvalidShare { participant: Alice, blame: Some(proof) }` is returned at `lib.rs:480-482`. The batch verifier (which would have flagged the PoP under `BatchId::Decryption`) is never executed.
5. Bob publishes `proof`. Anyone verifies it via `decrypt_with_proof`/`encryption_key_transcript` (the DLEq proves `proof.key = bX`), then runs `cipher::<C>(context, &bX).apply_keystream(Enc_AB.msg)` to recover Alice's secret share `s_AB` in cleartext. Blame resolves against Alice (`InvalidSignature` → sender at `lib.rs:585`), so the honest party is both slashed and has her share exposed.