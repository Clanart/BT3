### Title
PedPoP accepts the identity point as an encryption key and per-message key, collapsing the ECDH shared secret to a publicly-known constant - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The referenced bug class is an insecure default that silently disables a verification the rest of the code assumes is in place. Serai has exactly this shape in its point-decoding path: `Ciphersuite::read_G` performs only canonical-decoding checks and explicitly does **not** reject the identity element [1](#0-0) , while FROST defines a stricter `Curve::read_G` that rejects identity precisely because identity points are dangerous in these protocols [2](#0-1) . PedPoP, which is generic over `Ciphersuite` (not `Curve`), resolves `C::read_G` to the permissive `Ciphersuite` version everywhere: the registered encryption key in `EncryptionKeyMessage::read` [3](#0-2) , the per-message key in `EncryptedMessage::read` [4](#0-3) , and every polynomial commitment in `Commitments::read` [5](#0-4) . The identity is therefore accepted as a "secure" encryption key by default.

### Finding Description
In `verify_r1`, every participant's `EncryptionKeyMessage` is registered verbatim: `self.encryption.register(l, msg)` stores `msg.enc_key` in `decryption.enc_keys` with no identity or proof-of-possession check [6](#0-5) [7](#0-6) . Later, `generate_secret_shares` encrypts each participant's secret share to that registered key via `encrypt`, whose confidentiality rests entirely on `ecdh(key, to) = to * key` [8](#0-7) [9](#0-8) [10](#0-9) .

If a participant registers `enc_key = identity` (a valid canonical encoding accepted by `read_G`), then for every sender `ecdh(k_i, identity) = identity`. The ChaCha20 cipher key is derived deterministically from `context` and the *publicly-known* identity encoding, with a fixed IV (`b"DKG IV v0.2\0"`) [11](#0-10) . This produces two failures the code itself flags as fatal:

1. The shared secret is public: anyone can recompute `cipher(context, identity)` and decrypt every ciphertext addressed to that participant.
2. Key+IV reuse: all `n-1` senders encrypt to the same identity ECDH, yielding an identical keystream across distinct plaintexts — the code explicitly warns "Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be leaked" [12](#0-11) .

Additionally, `Commitments::read` accepts identity points in the commitment vector. The Schnorr PoK in `verify_r1` is verified against `msg.commitments[0]` [13](#0-12) , and `SchnorrSignature::verify` accepts any `R = sG` pair when the public key is identity [14](#0-13) , so the knowledge-of-discrete-log check is trivially satisfiable for an identity constant term — again a "proof" check that silently does nothing.

### Impact Explanation
PedPoP's design assumes the DKG runs over channels that are *authenticated* but not confidential — this is exactly why shares are ECDH-encrypted at all, and why `AdditionalBlameMachine::new` is explicitly built so that *non-participants* can process the encrypted messages [15](#0-14) . In Serai, these messages are gossiped/broadcast publicly. By submitting a single crafted `EncryptionKeyMessage` (public input reaching `EncryptionKeyMessage::read` / `register`), an unprivileged DKG participant makes **all** secret shares addressed to them decryptable by every observer, with no proof-of-possession, no blame proof, and no ECDH key revelation required — defeating the entire encryption layer. Any observer (including other participants) thereby recovers that participant's shares of every dealer polynomial, i.e., key-share recovery on the public channel. The same missing check also permits a participant to register another party's public key as their own, causing shares destined to them to be encrypted to a key they cannot open — enabling fraudulent blame/abort manipulation.

### Likelihood Explanation
Any participant in the DKG can do this: they only need to send a syntactically valid `EncryptionKeyMessage` whose trailing point is the canonical identity encoding. `read_G` accepts it, `register` stores it, and no PoP is ever requested for `enc_key` (the PoP exists only per `EncryptedMessage`, on `msg.key` — a fix the codebase itself documented as necessary, but only applied to the wrong field). The attack requires no collusion, no malformed encodings, and no integrator misuse — it is purely attacker-controlled bytes over the expected input path.

### Recommendation
Reject the identity element in all PedPoP deserialization/registration points. Concretely: after `C::read_G` in `EncryptionKeyMessage::read`, `EncryptedMessage::read`, and the `read_G` closure in `Commitments::read`, add `if point.is_identity().into() { Err(...) }` (mirroring `Curve::read_G` in `crypto/frost/src/curve/mod.rs`), or require the encryption key to be bound to a Schnorr PoK in the commitments message so a participant must prove knowledge of their `enc_key` discrete log. `Encryption::new` already guarantees honest keys are non-identity via `random_nonzero_F` [16](#0-15) , so this is purely a missing input-validation step.

### Proof of Concept
```rust
// Participant M runs the DKG honestly except for crafting its EncryptionKeyMessage.
// commitments_msg is a validly-signed Commitments<C> (even an all-identity vector
// passes the PoK check with R = sG, since verify uses commitments[0] as the key).

let mut crafted = vec![];
// Valid Commitments payload: t canonical points + valid SchnorrSignature
commitments.write(&mut crafted).unwrap();
// enc_key = identity: canonical encoding that Ciphersuite::read_G accepts
crafted.extend(<C::G as GroupEncoding>::to_bytes(&C::G::identity()).as_ref());

let msg = EncryptionKeyMessage::<C, Commitments<C>>::read(&mut crafted.as_slice(), params)?;
// Every honest participant calls:
secret_share_machine.encryption.register(M_participant, msg); // stores identity

// In generate_secret_shares, each honest sender i computes:
//   ecdh(k_i, identity) = identity   for ALL i
//   cipher(context, identity) -> identical publicly-derivable ChaCha20 keystream
// Any observer recomputes:
//   transcript("DKG Encryption v0.2" | context | "encryption_key" |
//              to_bytes(identity)) -> key;  iv = "DKG IV v0.2\0"
// and fully decrypts every EncryptedMessage addressed to M, plus obtains
// pairwise XORs of the plaintext shares due to keystream reuse.
```

Note on scope/uncertainty: I verified `read_G` permissiveness, the absent identity checks at registration, the ECDH/cipher construction, and the PoK path in the files cited. I did not exhaustively enumerate every downstream consumer of `Decryption::enc_keys` (e.g., all blame-proof variants), but the confidentiality collapse and publicly-derivable keystream follow directly from the cited code.

### Citations

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L99-100)
```rust
// Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be
// leaked.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-132)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-154)
```rust
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L433-445)
```rust
  pub(crate) fn new<R: RngCore + CryptoRng>(
    context: [u8; 32],
    i: Participant,
    rng: &mut R,
  ) -> Self {
    let enc_key = Zeroizing::new(C::random_nonzero_F(rng));
    Self {
      context,
      i,
      enc_pub_key: C::generator() * enc_key.deref(),
      enc_key,
      decryption: Decryption::new(context),
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L115-127)
```rust
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-315)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);
```

**File:** crypto/dkg/pedpop/src/lib.rs (L323-329)
```rust
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L366-370)
```rust
      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L639-661)
```rust
  /// Create an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was
  /// a member in the DKG protocol.
  ///
  /// Takes in the parameters for the DKG protocol and all of the participant's commitment
  /// messages.
  ///
  /// This constructor assumes the full validity of the commitment messages. They must be fully
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

**File:** crypto/schnorr/src/lib.rs (L88-109)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
```
