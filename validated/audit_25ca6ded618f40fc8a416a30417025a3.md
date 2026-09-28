### Title
PedPoP accepts identity point as a participant's ECDH encryption key, making secret shares publicly decryptable - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyMessage::read` and `EncryptedMessage::read` deserialize group elements with `Ciphersuite::read_G`, which enforces canonical encoding but permits the identity point. Unlike `Curve::read_G` in `crypto/frost/src/curve/mod.rs`, which explicitly rejects identity, the PedPoP decryption path performs no such check. A participant who registers the identity point as their encryption key causes every dealer to compute an ECDH shared secret equal to the identity, whose serialization is public — so the ChaCha20 keystream protecting that participant's secret shares is derivable by anyone who observes the ciphertext.

### Finding Description
The analog of CVE-2017-15580 ("accepts any content where a specific type was expected") is a deserialization path that accepts group elements which are structurally valid encodings but semantically invalid as public keys.

`EncryptionKeyMessage::read` reads the per-participant encryption key with `C::read_G`: [1](#0-0) 

`Decryption::register` stores it with no validity check: [2](#0-1) 

When a dealer later encrypts a share to that participant, `encrypt` computes `ecdh(key, to)` = `to * key`: [3](#0-2) 

If `to` is the identity, the ECDH result is the identity point for every dealer, and `cipher` derives the ChaCha20 key purely from `context` and `identity.to_bytes()`: [4](#0-3) 

Both inputs are public (context is a known session value; the identity encoding is fixed), so the keystream is publicly computable and the XOR-ed `SecretShare<F>` plaintext is recoverable by any observer. Shares and commitments flow through coordinator-published messages (`CoordinatorMessage::Shares` handled in `processor/src/key_gen.rs`), so the ciphertext is not private to the intended recipient: [5](#0-4) 

The same gap exists for `EncryptedMessage.key` (also `C::read_G`), though a malicious sender gains less from it.

### Impact Explanation
Every `EncryptedMessage` addressed to a participant who registered an identity encryption key can be decrypted by any party that sees it, yielding that participant's `SecretShare` from every dealer. This exposes the participant's final threshold secret share to the public, permanently reducing the multisig's security margin (one of the `t` shares needed for key reconstruction is known to everyone) and leaking honest dealers' polynomial evaluations, which are intended to be secret. The receiving participant cannot detect this: decryption, PoP verification, and share verification all still succeed, so the DKG completes and the group key is used under a silently degraded security level.

### Likelihood Explanation
Any participant can submit an identity `enc_key` inside their `EncryptionKeyMessage`; `Commitments`/`EncryptionKeyMessage::read` accepts it, `register` stores it, and `encrypt` uses it without complaint. It requires the key-registering participant to be malicious or their input corrupted, and requires an observer to see the share ciphertext — which in the coordinator flow is broadcast data. Confirmed reachable through `EncryptedMessage::<C, SecretShare<C::F>>::read` and `EncryptionKeyMessage::read` in `processor/src/key_gen.rs`.

### Recommendation
Reject the identity point for all keys used in ECDH. In `EncryptionKeyMessage::read` and `EncryptedMessage::read`, after `C::read_G`, check `point.is_identity()` and error out (mirroring `Curve::read_G` in `crypto/frost/src/curve/mod.rs` lines 125–131). Additionally, `Decryption::register` should assert the registered `enc_key` is non-identity so misuse of the internal API fails loudly.

### Proof of Concept
```rust
// Participant l crafts their commitment message with an identity enc_key.
let mut msg = EncryptionKeyMessage::<C, Commitments<C>>::read(&mut bytes, params).unwrap();
// Serialize identity as enc_key (canonical encoding of C::G::identity()).
// Honest dealer i then does:
//   self.encryption.register(l, msg);               // stores identity key, no error
//   self.encryption.encrypt(rng, l, share_bytes);   // ecdh(key, identity) == identity
// Inside encrypt: cipher(context, identity.to_bytes()) -> public ChaCha20 keystream.
// Anyone observing the resulting EncryptedMessage computes the same keystream and
// XORs it against msg.msg to recover SecretShare<C::F>(share-at-l) in the clear.
```
No PoP or verification step fails: the dealer's share verification statements and the recipient's `calculate_share` batch verification operate on the decrypted share normally, so the protocol completes with the leak undetected.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-111)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}

// Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be
// leaked.
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-156)
```rust
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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
```

**File:** processor/src/key_gen.rs (L402-413)
```rust
          // Parse the shares
          let mut shares = HashMap::new();
          for i in 1 ..= params.n() {
            let i = Participant::new(i).unwrap();
            let Some(share) = shares_ref.get_mut(&i) else { continue };
            shares.insert(
              i,
              EncryptedMessage::<C, SecretShare<C::F>>::read(share, params).map_err(|_| {
                ProcessorMessage::InvalidShare { id, accuser: params.i(), faulty: i, blame: None }
              })?,
            );
          }
```
