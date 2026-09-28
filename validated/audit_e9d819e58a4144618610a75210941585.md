### Title
Missing proof-of-possession on registered DKG encryption key lets one participant bind another participant's key and disclose their secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the GitLab missing-authorization disclosure, PedPoP's `EncryptionKeyMessage` carries a long-term ECDH public key (`enc_key`) with no proof of possession. `Decryption::register` stores whatever `enc_key` a participant supplies. A participant (Eve) can register the encryption public key of another participant (Alice). Every other participant then encrypts the secret shares intended for Eve to a key Eve cannot read but Alice can — disclosing confidential shares to an unauthorized party.

### Finding Description
`EncryptionKeyMessage` is defined as just `{ msg, enc_key }` with `enc_key` read/written raw and never verified [1](#0-0) . `Decryption::register` inserts it unconditionally [2](#0-1) . In `SecretShareMachine::verify_r1`, only the coefficient-zero Schnorr PoK (`msg.sig`) is batch-verified; `enc_key` is registered without any PoK [3](#0-2) . Senders then encrypt each share via `encryption.encrypt(rng, l, share_bytes)`, which ECDHs a fresh per-message key against the registered `enc_keys[l]` [4](#0-3) [5](#0-4) . The per-message PoP added to `EncryptedMessage` only proves knowledge of the ephemeral message key — it mitigates the blame side-effect described in the spec but does not prevent a registered `enc_key` from being someone else's key [6](#0-5) .

### Impact Explanation
Because DKG share ciphertexts are distributed through the coordinator (all `DkgShares` payloads are accumulated and stored), Alice observes the ciphertexts addressed to Eve, which were ECDH-encrypted under Alice's own `enc_key` private scalar. Alice decrypts every sender's evaluation `f_j(Eve_index)`, sums them, and recovers Eve's secret key share `s_Eve` — full key-share recovery of an honest participant, plus the ability to later sign on Eve's behalf or blame-frame her. This matches the report class exactly: confidential data (secret shares) disclosed to a party not authorized to see them, caused by a missing ownership check on the encryption key registration.

### Likelihood Explanation
Requires Eve to be a DKG participant who submits a crafted `EncryptionKeyMessage` — untrusted bytes fully reachable via `EncryptionKeyMessage::read` / `Decryption::register`. No collusion or broken transport assumptions are needed; the only requirement is that Eve observes or learns Alice's published `enc_key` (public) and that ciphertexts are routed/stored where Alice can read them, which holds in the coordinator flow [7](#0-6) .

### Recommendation
Include a Schnorr proof-of-possession for `enc_key` in `EncryptionKeyMessage`, challenged over the DKG `context` and participant index, and batch-verify it in `SecretShareMachine::verify_r1` (and reject in `AdditionalBlameMachine::new`/`Decryption::register` paths) so a participant can only register a key whose discrete log they know.

### Proof of Concept
1. Alice (index a) runs `Encryption::new`, publishing `enc_pub_key_a = enc_key_a * G` in her commitment message.
2. Eve (index e) submits her `EncryptionKeyMessage` with `enc_key = enc_pub_key_a` instead of her own. `verify_r1` accepts it: the only signature checked is the PoK on `commitments[0]`.
3. Every honest sender j calls `generate_secret_shares`; for recipient e it computes `share = polynomial(coeffs_j, e)` and `encrypt`s it as `cipher(context, ecdh(k_j, enc_pub_key_a))`.
4. Alice collects the ciphertexts addressed to e and computes `ecdh(enc_key_a, msg.key)` for each, decrypting `f_j(e)` for all j.
5. Alice sums the decrypted shares, obtaining `s_e = Σ_j f_j(e)` — Eve's secret share — verified against `s_e * G == verification_shares[e]`. Eve cannot detect this: she simply fails decryption and any blame she attempts produces `EncryptionKeyProof` she cannot generate (she lacks `enc_key_a` private), so `blame_internal` returns `recipient` (Eve) as the faulty party [8](#0-7) , compounding disclosure with wrongful blame.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L49-64)
```rust
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyMessage<C: Ciphersuite, M: Message> {
  msg: M,
  enc_key: C::G,
}

// Doesn't impl ReadWrite so that doesn't need to be imported
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L80-92)
```rust
#[derive(Clone, Zeroize)]
pub struct EncryptedMessage<C: Ciphersuite, E: Encryptable> {
  key: C::G,
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
  msg: Zeroizing<E>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-156)
```rust
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-331)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
```

**File:** crypto/dkg/pedpop/src/lib.rs (L582-608)
```rust
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }

    // The share was canonical and valid
    recipient
```

**File:** coordinator/src/tributary/handle.rs (L402-416)
```rust
        let data_spec = DataSpecification { topic: Topic::Dkg, label: Label::Share, attempt };
        let encoded_data = (confirmation_nonces.to_vec(), our_shares.encode()).encode();
        match self.handle_data(&removed, &data_spec, &encoded_data, &signed) {
          Accumulation::Ready(DataSet::Participating(confirmation_nonces_and_shares)) => {
            log::info!("got all DkgShares for {}", hex::encode(genesis));

            let mut confirmation_nonces = HashMap::new();
            let mut shares = HashMap::new();
            for (participant, confirmation_nonces_and_shares) in confirmation_nonces_and_shares {
              let (these_confirmation_nonces, these_shares) =
                <(Vec<u8>, Vec<u8>)>::decode(&mut confirmation_nonces_and_shares.as_slice())
                  .unwrap();
              confirmation_nonces.insert(participant, these_confirmation_nonces);
              shares.insert(participant, these_shares);
            }
```
