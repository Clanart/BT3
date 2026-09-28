### Title
PedPoP encryption key registration lacks proof-of-possession, allowing a participant to redirect another party's secret shares to a chosen decryption key - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
In PedPoP (Serai's Pedersen DKG), each participant publishes an `EncryptionKeyMessage` carrying `enc_key: C::G`, the ECDH recipient key used to encrypt that participant's secret shares. `EncryptionKeyMessage::read` accepts `enc_key` with only `C::read_G` (canonicality/identity checks), and `Encryption::registration` / `Decryption::register` insert it with no proof-of-possession and no binding to the claiming participant beyond the outer authenticated channel. The per-message `pop` Schnorr proof inside `EncryptedMessage` only binds the ephemeral per-message key — not the long-term `enc_key`. A participant can therefore register someone else's (or a colluding party's) public encryption key as their own, causing all dealers to encrypt that participant's shares to a private key the attacker-collaborator holds — an improper-authorization/key-co-option leak of DKG share data, analogous to unprivileged users reaching data meant for others via a missing access check.

### Finding Description [1](#0-0) 
`EncryptionKeyMessage` is `{ msg, enc_key }` — `enc_key` is a bare group element; `read` performs no PoP and there is no signature over it. [2](#0-1) 
`registration` emits `enc_pub_key` inside the round-1 message unsigned w.r.t. the key's secret, and `encrypt` encrypts shares to `self.decryption.enc_keys[&participant]` — trusting whatever key the participant registered. [3](#0-2) 
`Decryption::register` only asserts no double-registration for the same `Participant`; it never checks that the key differs from other participants' keys nor that the registrant knows its discrete log.

The codebase itself acknowledges this co-option class: the spec describes the attack where "a malicious adversary claims another participant's encryption key," and mitigates only the *blame-revelation* side effect via the per-message PoP in `EncryptedMessage` (comments at `encryption.rs:83-90`). But that PoP does nothing to stop the direct leak: shares encrypted to a co-opted key are trivially decryptable by whoever knows that key's secret.

### Impact Explanation
In a `t`-of-`n` DKG, if participant B registers participant A's `enc_key` (publicly broadcast in round 1), every dealer encrypts B's share to A's key. A — or anyone holding that key's discrete log, including a "guest" observer who was given it — recovers the plaintext `SecretShare` evaluated at index B, i.e., another participant's key share contribution, without any authorization check on the share itself. If the key generation is nonetheless completed/retried with attacker-selected keys, the attacker accumulates shares at multiple indexes, effectively reducing the threshold for the resulting `ThresholdKeys` (key share recovery of an unintended party). At minimum it is a confidentiality break of per-participant share data meant to be readable only by its intended recipient — mirroring the advisory's "guest obtains sensitive object data through missing authorization."

### Likelihood Explanation
Requires a participant (or colluding pair) in the DKG to replay another participant's broadcast `enc_key` as their own — pure public-input manipulation, no secret knowledge needed for the copy itself. The attack is detectable only in that B cannot decrypt its own shares (protocol abort for B), so the secrecy gain only pays off if the resulting key set is used despite B's exclusion or in a re-run; accordingly Medium, matching the source advisory's severity.

### Recommendation
Bind `enc_key` to the registering participant with a proof-of-possession: either extend the existing coefficient-zero Schnorr PoK `challenge` (`lib.rs` ~line 328) to also cover `enc_key` (it is already hashed into `cached_msg` only if included in `Commitments`), or add a dedicated Schnorr signature over `context || participant || enc_key` inside `EncryptionKeyMessage`, verified in `verify_r1`/`Decryption::register`. Optionally reject duplicate `enc_key` values across participants as cheap defense-in-depth.

### Proof of Concept
1. Run `SecretShareMachine::new`/`generate_secret_shares` for `n` participants (see `tests.rs` `commit_enc_keys_and_shares`).
2. Attacker B constructs its round-1 `EncryptionKeyMessage` with `enc_key` set to participant A's broadcast `enc_pub_key` (`EncryptionKeyMessage { msg: B_commitments, enc_key: A_enc_pub }`) — no PoP is required, so this parses and registers cleanly via `Decryption::register`.
3. Every honest dealer calls `encryption.encrypt(rng, B, share_B)`, which ECDH's against A's key.
4. A (holder of the corresponding `enc_key` scalar) computes `ecdh(&a_enc_key, msg.key)` and `cipher(context, &key).apply_keystream(msg.msg)` — recovering B's plaintext `SecretShare` from the wire bytes, data B alone was authorized to read.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L48-59)
```rust
/// Wraps a message with a key to use for encryption in the future.
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L448-467)
```rust
  pub(crate) fn registration<M: Message>(&self, msg: M) -> EncryptionKeyMessage<C, M> {
    EncryptionKeyMessage { msg, enc_key: self.enc_pub_key }
  }

  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    self.decryption.register(participant, msg)
  }

  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```
