### Title
Forged PedPoP blame accusation pins fault on an honest participant using entirely attacker-fabricated messages - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report describes a user-controlled variable causing a security-sensitive read of unintended data. The analog in Serai is `Decryption::decrypt_with_proof` / `BlameMachine::blame_internal`: an attacker-controlled `EncryptedMessage` and `EncryptionKeyProof` are fed to the blame-verification path, and the `sender`/`recipient` identifiers used to bind the proof-of-possession and select the ECDH public key are attacker-chosen transcript inputs rather than authenticated protocol identities. An unprivileged observer of a PedPoP DKG can fabricate a complete, self-consistent accusation (ciphertext, PoP, ECDH key, DLEq proof) that verifies correctly and deterministically returns an honest `sender` as the faulty party.

### Finding Description
Blame evaluation in `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:575-609`) calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)`, which performs two checks (`crypto/dkg/pedpop/src/encryption.rs:366-397`):

1. `msg.pop.verify(msg.key, pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg))` — a Schnorr proof of possession over `msg.key` whose challenge (`encryption.rs:302-324`) binds only `context`, `nonce`, `key`, `sender`, and `msg`. `sender` (`from`) is a `Participant` supplied by the caller of `blame`, i.e., part of the accusation, not authenticated inside the proof. Any party knowing the discrete log of `msg.key` can produce a valid PoP claiming any `from`.
2. `proof.dleq.verify(&mut encryption_key_transcript(self.context), &[C::generator(), msg.key], &[self.enc_keys[&decryptor], *proof.key])` — proves `dlog_G(msg.key) == dlog_{enc_keys[decryptor]}(proof.key)`. The transcript (`encryption.rs:326-330`) binds only `context`, so the proof is valid for *any* `msg.key` of the accuser's choosing: pick scalar `x`, set `msg.key = G*x` and `proof.key = x*enc_keys[decryptor]`, both computable from public data.

Since the accuser controls `msg.msg` (the ciphertext) and knows `proof.key`, they compute the ChaCha20 keystream themselves via `cipher::<C>(self.context, &proof.key)` (`encryption.rs:392`) and can set the decrypted `SecretShare` plaintext to an arbitrary canonical scalar that is *not* a valid share. Back in `blame_internal` (`lib.rs:590-605`), `from_repr` succeeds, `share_verification_statements` against `self.commitments[&sender]` fails, and the function returns `sender` — the honest, accused participant — as faulty.

`AdditionalBlameMachine::new` (`lib.rs:649-662`) only requires the `context`, `n`, and the round-1 `EncryptionKeyMessage<Commitments>` messages, which are broadcast to all parties. Any observer holding them can register the public `enc_key`s and evaluate `blame` on a fully fabricated accusation.

### Impact Explanation
The accepted acceptance criterion is a forged proof: here the attacker forges a *blame verdict*. `BlameMachine::blame`/`AdditionalBlameMachine::blame` return a `Participant` that callers treat as the authenticated faulty party (the `msg` docstring requires the accusation be authenticated as coming from the accused sender, but nothing inside the cryptographic verification enforces this — the PoP is satisfiable by anyone for any claimed `from`). In the Serai stack, DKG blame feeds validator slashing (`processor/src/slash_report_signer.rs`), so a forged verdict can cause an honest validator to be blamed, excluded, or slashed, and can abort an otherwise successful key generation.

### Likelihood Explanation
Requires only public DKG transcripts: the round-1 commitment/encryption-key messages (broadcast), the `context`, and `n`. No private keys, no participation in the DKG, and no cooperation from the victim are needed — the accuser fabricates `EncryptedMessage` and `EncryptionKeyProof` locally. Cost is a Schnorr signature and a DLEq proof.

### Recommendation
Bind the accusing recipient and the message ciphertext into the artifacts the accuser cannot forge. Concretely:
- Have the accuser's revealed `EncryptionKeyProof` only ever be produced by the *recipient's* machine (as in `Encryption::decrypt` at `encryption.rs:469-501`), and have `blame`/`AdditionalBlameMachine::blame` verify that the accusation is accompanied by a signature from `recipient`'s registered `enc_key` (or session key) over `(sender, recipient, msg)`, proving the accuser actually received and chose to reveal that message's ECDH key.
- Alternatively, require `proof.key` be bound to the message's original `key`/PoP such that the revealed ECDH can only be produced by the holder of `enc_keys[recipient]`'s private scalar — e.g., require the DLEq to additionally prove knowledge over a transcript that includes a signature by the recipient's encryption key on the accusation tuple. A third party can compute `x*enc_pub` without knowing the private key, so the binding must incorporate something requiring `enc_key` itself (the recipient's PoP/registration signature over the specific accusation).
- Add the `recipient` identity and the ciphertext hash into `pop_challenge`/`encryption_key_transcript` so fabricated `from`/`to` tuples are not interchangeable.

### Proof of Concept
```rust
// Given: public context, n, and broadcast round-1 msgs
// EncryptionKeyMessage<C, Commitments<C>> for all participants.
let mut blame = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

// Victims: honest sender Alice, honest recipient Bob.
let alice = Participant::new(ALICE).unwrap();
let bob   = Participant::new(BOB).unwrap();

// Fabricate an EncryptedMessage to Bob "from Alice".
let x = Zeroizing::new(C::random_nonzero_F(&mut OsRng));   // known scalar
let msg_key = C::generator() * x.deref();                  // msg.key = G*x
let bob_enc_pub = /* enc_key from Bob's EncryptionKeyMessage */;

// Choose plaintext: a canonical scalar that is NOT a valid share of Alice.
let fake_plain = C::F::ONE;
let mut msg_bytes = SecretShare::<C::F>(fake_plain.to_repr());

// Attacker knows proof.key = x * bob_enc_pub, so they compute the keystream.
let shared = Zeroizing::new(bob_enc_pub * x.deref());
cipher::<C>(context, &shared).apply_keystream(msg_bytes.as_mut().as_mut());

// PoP over msg.key, signing for `from = alice` — attacker knows dlog of msg.key.
let nonce = Zeroizing::new(C::random_nonzero_F(&mut OsRng));
let pop = SchnorrSignature::sign(
  &x, nonce.clone(),
  pop_challenge::<C>(context, C::generator() * nonce.deref(), msg_key, alice, msg_bytes.as_ref()),
);
let forged_msg = EncryptedMessage { key: msg_key, pop, msg: Zeroizing::new(msg_bytes) };

// DLEq proving dlog_G(msg.key) == dlog_{bob_enc_pub}(shared) — a true statement.
let proof = EncryptionKeyProof {
  key: shared,
  dleq: DLEqProof::prove(&mut OsRng, &mut encryption_key_transcript(context),
                         &[C::generator(), msg_key], &x),
};

// blame verifies: pop OK (attacker signed it), DLEq OK, decrypted share is a
// canonical scalar that fails share verification => returns `alice`.
assert_eq!(blame.blame(alice, bob, forged_msg, Some(proof)), alice);
```

The honest participant `alice` is returned as the faulty party despite never sending anything, because the accusation's `sender`/`recipient` binding and the ECDH key are satisfiable purely from public broadcast data.