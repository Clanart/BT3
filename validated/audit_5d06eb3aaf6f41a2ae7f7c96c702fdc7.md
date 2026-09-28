### Title
Blame proof reveals the ECDH shared secret, letting any holder decrypt the accused secret share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Arcadia report describes a check that treats a broad credential (max allowance intended for account-management operations) as authorization for a different, more powerful action (driving `flashAction`). Serai's PedPoP DKG has the same shape: the `EncryptionKeyProof` produced to adjudicate a blame claim is a *general decryption credential*. It contains `proof.key`, the ECDH shared point between the accused sender and the accuser, and `Decryption::decrypt_with_proof` accepts possession of that point as sufficient authorization to decrypt the underlying `EncryptedMessage`. Anyone who obtains the serialized proof plus the ciphertext can recover the plaintext secret share — the proof authorizes far more than "verify that decryption was honest."

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`:

- `Encryption::decrypt` (encryption.rs:469-501) computes `key = ecdh::<C>(&self.enc_key, msg.key)` — the shared ECDH point — decrypts the message with `cipher::<C>(self.context, &key)`, and then returns an `EncryptionKeyProof { key, dleq }` where `key` is that same shared decryption secret embedded as a public `C::G` field. The DLEq proves only that `proof.key` is the correct ECDH output for `msg.key` and the accuser's `enc_key`; it does not restrict who may use it.
- `Decryption::decrypt_with_proof` (encryption.rs:366-397) verifies the per-message PoP signature and the DLEq (`proof.dleq.verify(..., &[C::generator(), msg.key], &[self.enc_keys[&decryptor], *proof.key])`), then performs `cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg...)` — i.e., possession of `proof.key` alone fully decrypts the message.
- `EncryptionKeyProof::read`/`write`/`serialize` (encryption.rs:266-280) make this credential a plain serializable object; `BlameMachine` in `crypto/dkg/pedpop/src/lib.rs:524-531` exists precisely to hand these proofs to other parties for adjudication.

So the object meant to let a third party *check* a decryption is byte-for-byte the key that performs the decryption. This is the Serai analog of "one authorization artifact (allowance) silently authorizing a broader action": the blame artifact doubles as a universal decryption token for that message.

### Impact Explanation
The plaintext inside each `EncryptedMessage<C, SecretShare<C::F>>` is a PedPoP secret share of the threshold key (`generate_secret_shares`, pedpop/src/lib.rs:366-369; consumed in `calculate_share`, lib.rs:476-484). If a blame proof is shared beyond a single adjudicator — the natural way to get multiple parties to agree on fault — every holder can decrypt the corresponding share. Combined with the ciphertext (or with a relayed copy of `msg`), an observer recovers a participant's key share. Repeating for enough shares yields threshold key-share recovery / unintended disclosure of secret material, which the rules accept as concrete impact.

### Likelihood Explanation
Triggering requires a blame event: either a genuinely faulty share or an accuser willing to open their decrypted share. An accuser motivated to leak (or a buggy integration that publishes blame proofs to a bulletin board) causes disclosure with a single `serialize()`. The ciphertext is nominally sent over an authenticated channel, so a pure network observer needs access to the transmitted `EncryptedMessage` bytes as well; the leaked credential itself carries no usage restriction, no expiry, and no binding to the adjudicator. Medium likelihood under realistic deployments where blame proofs and DKG transcripts are logged or broadcast for consensus.

### Recommendation
Do not place the raw ECDH shared point in `EncryptionKeyProof`. Instead:

- Prove decryption correctness in zero knowledge (e.g., a DLEq/Chaum-Pedersen proof that `msg` decrypts to a specific value under `enc_key`, or reveal only `cipher(context, key)` applied and let the checker recompute it locally without serializing `key`).
- If revealing the key is unavoidable, bind the proof to the adjudicating party so it is useless to third parties, and document that publishing it discloses the share.
- At minimum, add a `#[doc]` warning on `EncryptionKeyProof` and `BlameMachine` that the proof contains the message decryption key.

### Proof of Concept
```rust
// crypto/dkg/pedpop scenario, C: Ciphersuite
// Attacker holds: the EncryptedMessage bytes routed to victim `v`,
// and the serialized EncryptionKeyProof `v` published to blame sender `s`.

let params = ThresholdParams::new(t, n, v).unwrap();
let msg: EncryptedMessage<C, SecretShare<C::F>> =
    EncryptedMessage::read(&mut ciphertext.as_slice(), params).unwrap();
let proof: EncryptionKeyProof<C> =
    EncryptionKeyProof::read(&mut blame_proof.as_slice()).unwrap();

// proof.key is the ECDH shared point; decrypt_with_proof applies it directly
// (encryption.rs:392): cipher::<C>(context, &proof.key).apply_keystream(msg)
// Equivalent manual decryption:
let shared = proof.key;                       // ecdh(enc_key_v, msg.key)
cipher::<C>(context, &shared).apply_keystream(msg.msg.as_mut().as_mut());
let share = C::F::from_repr(msg.msg.0).unwrap(); // sender s's share for victim v, recovered
```

Root cause: `EncryptionKeyProof` conflates "evidence the decryption key is correct" with the key itself (`key` field, encryption.rs:262-264), and `decrypt_with_proof` (encryption.rs:392) treats possession of `proof.key` as sufficient to decrypt — an authorization artifact valid for a broader action than intended, matching the reported bug class.