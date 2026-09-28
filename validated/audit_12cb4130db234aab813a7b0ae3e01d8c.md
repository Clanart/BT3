### Title
Missing validation of registered encryption keys allows any observer to recover a participant's DKG secret share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the missing `checkLink`/`checkExec` gate in the advisory (a missing security-manager check letting untrusted input reach a privileged operation), PedPoP never validates the per-participant encryption public key carried in `EncryptionKeyMessage`. `EncryptionKeyMessage::read` accepts any canonical point for `enc_key`, and `Decryption::register` stores it with no checks at all. A participant that registers the group identity (or any low-order/torsion point) as their encryption key causes every secret share encrypted to them to be decryptable by anyone who observes the ciphertext, because the ECDH output becomes the identity point — a publicly known value.

### Finding Description
`EncryptionKeyMessage::read` reads `enc_key` with `C::read_G`, which only enforces canonical encoding — the identity point is a valid canonical point [1](#0-0) . `Decryption::register` inserts it into `enc_keys` with only a duplicate-registration assert, no identity/subgroup/non-zero check [2](#0-1) . Later, `encrypt` computes `ecdh::<C>(&key, to)` where `to` is the recipient's registered `enc_key` [3](#0-2) . If `to` is the identity, the ECDH shared point is the identity for every message, so `cipher()` derives a ChaCha20 key from a transcript over the publicly known identity encoding [4](#0-3) . Anyone holding the `EncryptedMessage` ciphertext can recompute this key and apply the keystream to recover the plaintext `SecretShare`. The per-message PoP (`pop_challenge`) binds sender and message but not the recipient key, so it does not prevent this.

The protocol only assumes *authenticated* channels for these messages — the encryption layer exists precisely because the channel is not confidential ("This still doesn't mean the DKG offers an authenticated channel" refers to message origin, and shares are explicitly encrypted "to protect the secret shares") [5](#0-4) . Nothing in `verify_r1` inspects `enc_key` before it is used [6](#0-5) .

### Impact Explanation
All `EncryptedMessage<C, SecretShare>` ciphertexts addressed to a participant who registered an identity/low-order `enc_key` are decryptable by any unprivileged observer (coordinator, other participants, or a passive network listener on the authenticated channel). The observer recovers that participant's full FROST secret share — i.e., key share recovery without the victim's consent. In a t-of-n scheme this silently degrades the threshold: an attacker who additionally compromises or colludes with `t-1` other share slots obtains the group secret. For low thresholds (e.g., 2-of-n) a single leaked share combined with one compromised signer is total key recovery.

### Likelihood Explanation
Requires one participant (malicious or compromised/misconfigured) to register a degenerate encryption key in their broadcast `EncryptionKeyMessage`, and an observer able to read the addressed ciphertexts. Since the library explicitly does not provide confidential channels and senders are not given any check to detect the bad key, honest participants will encrypt to it without warning. The attack is fully passive for the beneficiary.

### Recommendation
In `EncryptionKeyMessage::read` or `Decryption::register`, reject `enc_key` values that are the identity (`bool::from(enc_key.is_identity())`) and, for non-prime-order curves, enforce a prime-order subgroup check (e.g., verify `enc_key` is torsion-free). Optionally require a Schnorr proof of possession of the encryption key at registration so degenerate or reused keys are detectable.

### Proof of Concept
```rust
// Participant l broadcasts an EncryptionKeyMessage whose enc_key is the identity.
// read_G accepts it (identity is a canonical encoding):
//   EncryptionKeyMessage { msg: Commitments{...}, enc_key: C::G::identity() }
// verify_r1 -> self.encryption.register(l, msg) stores identity as l's key.

// Every other participant then does, in generate_secret_shares:
//   self.encryption.encrypt(rng, l, share_bytes)
// -> ecdh(&key, C::G::identity()) == C::G::identity()
// -> cipher() derives ChaCha20 key from transcript containing the identity encoding,
//    which is PUBLIC knowledge.

// Observer (any party that sees the authenticated-channel ciphertext) recovers the share:
//   let shared = C::G::identity();                      // known ECDH result
//   let mut keystream = cipher::<C>(context, &Zeroizing::new(shared));
//   let mut plaintext = encrypted_message.msg.clone();
//   keystream.apply_keystream(plaintext.as_mut().as_mut());
//   let share = C::F::from_repr(plaintext.0).unwrap(); // participant l's secret share
```

Uncertain aspects I could not fully verify: whether deployments treat the share channel as confidential in addition to authenticated (which would reduce, but not eliminate, exposure to other participants/relays), and whether any in-scope ciphersuite's `GroupEncoding::from_bytes` rejects identity — none do by default.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-133)
```rust
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();

  let zeroize = |buf: &mut [u8]| buf.zeroize();

  let mut key = Cc20Key::default();
  let mut challenge = transcript.challenge(b"key");
  key.copy_from_slice(&challenge[.. 32]);
  zeroize(challenge.as_mut());

  // Since the key is single-use, it doesn't matter what we use for the IV
  // The issue is key + IV reuse. If we never reuse the key, we can't have the opportunity to
  // reuse a nonce
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
  zeroize(key.as_mut());
  res
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L299-301)
```rust
// This doesn't need to take the msg. It just doesn't hurt as an extra layer.
// This still doesn't mean the DKG offers an authenticated channel. The per-message keys have no
// root of trust other than their existence in the assumed-to-exist external authenticated channel.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-320)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

```
