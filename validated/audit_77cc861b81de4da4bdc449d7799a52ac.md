### Title
Unauthenticated DKG messages can extract a recipient’s per-message ECDH key before proof-of-possession verification - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
`Encryption::decrypt` queues the message proof-of-possession for asynchronous batch verification, but immediately performs ECDH with the recipient’s static encryption key and constructs a serializable `EncryptionKeyProof` before the queued PoP is verified. `KeyMachine::calculate_share` then returns that proof early if the decrypted bytes are not a canonical scalar, so an attacker can copy an honest sender’s per-message key into a forged message and obtain the ECDH key needed to decrypt the honest sender’s original share.

### Finding Description
`Encryption::decrypt` first calls `msg.pop.batch_verify`, then computes `ecdh(&self.enc_key, msg.key)`, decrypts the ciphertext, and returns an `EncryptionKeyProof` containing the ECDH point plus a DLEq proof before the batch verifier is run. The PoP challenge binds the claimed sender and ciphertext, so copying another message’s `key` into a forged message cannot produce a valid PoP, but that check is only deferred in this path. In `calculate_share`, a non-canonical decrypted share immediately returns `PedPoPError::InvalidShare` with `Some(blame)` containing the already-created `EncryptionKeyProof`, bypassing the later `batch.verify_with_vartime_blame` call that would have rejected the invalid PoP. The returned proof serializes the ECDH key, and `decrypt_with_proof` confirms that this key is sufficient to decrypt a message once it is proven to correspond to the recipient’s registered encryption key and the message key.

Relevant code:
- Deferred PoP verification and premature ECDH/proof generation: `crypto/dkg/pedpop/src/encryption.rs:469-499`
- Publicly serializable ECDH proof: `crypto/dkg/pedpop/src/encryption.rs:259-279`
- Early error returning blame before batch verification: `crypto/dkg/pedpop/src/lib.rs:474-499`
- Public error carrying `Option<EncryptionKeyProof>`: `crypto/dkg/pedpop/src/lib.rs:36-48`
- ECDH key decryption oracle behavior: `crypto/dkg/pedpop/src/encryption.rs:381-393`

### Impact Explanation
An attacker can recover an encrypted secret share intended for another participant. They observe an honest sender’s serialized `EncryptedMessage`, copy its per-message public key into a forged message under the attacker’s participant index, attach an arbitrary invalid PoP and ciphertext, and submit it to the recipient’s `calculate_share`. If the forged ciphertext decrypts to a non-canonical scalar, the recipient returns an `EncryptionKeyProof` for the copied message key, even though the attacker never possessed that key. Using the exposed ECDH point as the ChaCha20 shared key decrypts the honest sender’s original ciphertext and reveals the secret share. This is a concrete key-share disclosure to an unauthenticated party, not merely a malformed-message rejection.

### Likelihood Explanation
The attack is reachable through untrusted `EncryptedMessage` bytes accepted by `calculate_share`; no validator privilege, leaked key, colluding threshold, or network-layer TLS position is required. The PoP failure is guaranteed, but the premature-return path requires the forged ciphertext to decrypt to bytes rejected by `C::F::from_repr`. For a roughly 252-bit Ristretto scalar in a 32-byte encoding, random plaintext is non-canonical with high probability, so a forged ciphertext will normally trigger the vulnerable early return. Repeated attempts may require new DKG contexts or protocol retries because `calculate_share` consumes the machine and returns an error.

### Recommendation
Authenticate the per-message key before deriving or disclosing the ECDH key. At minimum, verify `msg.pop` synchronously before `ecdh`, or delay all ECDH/key-proof construction until the queued PoP batch has succeeded. If deferred verification is required for performance, ensure no early error path can expose `EncryptionKeyProof` or decrypted content until after `batch.verify_with_vartime_blame` confirms the PoP. The blame flow can still return `None` for an invalid PoP, matching the existing `BatchId::Decryption` convention.

### Proof of Concept
```rust
// Attacker observes an honest serialized EncryptedMessage from Alice to Bob:
// format: msg.key || pop.R || pop.s || ciphertext
let alice_msg: Vec<u8> = observed_alice_to_bob_message();
let key_len = <Ristretto as Ciphersuite>::G::Repr::default().as_ref().len();

let mut forged = Vec::new();
// Copy Alice's per-message ECDH public key.
forged.extend_from_slice(&alice_msg[..key_len]);

// Any structurally valid but cryptographically invalid Schnorr signature.
forged.extend_from_slice(&[0u8; 64]); // replaced with canonical R and s encodings

// Fixed-size SecretShare ciphertext. For Ristretto this is normally 32 bytes.
forged.extend_from_slice(&random_32_bytes());

let mut cursor = forged.as_slice();
let forged_msg =
    EncryptedMessage::<Ristretto, SecretShare<Scalar>>::read(&mut cursor, params)
        .unwrap();

let mut shares = HashMap::new();
shares.insert(attacker_participant, forged_msg);

// Bob calls calculate_share. decrypt() queues the invalid PoP, but immediately
// calculates Bob_enc_key * Alice_msg_key and creates an EncryptionKeyProof.
match bob_key_machine.calculate_share(&mut rng, shares) {
    Err(PedPoPError::InvalidShare { blame: Some(proof), .. }) => {
        // Usually reached when the forged ciphertext decrypts to a
        // non-canonical scalar. The proof exposes the ECDH point before the
        // invalid PoP is checked.
        let disclosed = proof.serialize();

        // disclosed begins with Bob_enc_key * Alice_msg_key. Feeding that
        // shared point into the same DKG cipher transcript decrypts the
        // original Alice-to-Bob ciphertext, revealing Alice's share for Bob.
    }
    _ => {}
}
```

The root cause is not that ECDH is performed—static ECDH is the protocol design—but that authentication of the supplied per-message key is deferred until after the recipient has already calculated and packaged the reusable shared decryption key.