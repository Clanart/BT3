### Title
Unauthenticated DKG encryption key in `EncryptionKeyMessage` allows MitM substitution to intercept secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The DIRAC advisory concerns code accepted over a channel whose authenticity check (the checksum) is itself fetched over the same unvalidated channel, leaving the payload effectively unverified. The Serai analog is in PedPoP round 1: each participant's long-term ECDH encryption key `enc_key` is carried inside `EncryptionKeyMessage` alongside the `Commitments`, yet the Schnorr proof-of-knowledge signature that authenticates the message covers only the commitment points (`cached_msg`), not `enc_key`. An attacker who can alter the broadcast message bytes in transit can swap `enc_key` for their own public key without invalidating the signature; every other participant then encrypts that victim's secret shares to the attacker's key.

### Finding Description
`EncryptionKeyMessage::read` deserializes the inner `Commitments` message and then reads `enc_key` with `C::read_G`, with no proof of possession and no signature coverage over `enc_key`. [1](#0-0) 

`Commitments::read` builds `cached_msg` solely from the `t` commitment points; the appended `sig` is read afterward and is not bound to `enc_key`. [2](#0-1) 

The PoK challenge is computed as `challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg)` — i.e. over context, participant index, nonce, and `cached_msg` (commitments only). `enc_key` never enters the transcript. [3](#0-2) 

`Decryption::register` stores `msg.enc_key` for that participant unconditionally after the (still-valid) PoK batch verification passes. [4](#0-3) 

Subsequently, `Encryption::encrypt` encrypts the secret share destined for that participant with `cipher(context, ecdh(per_message_key, enc_keys[&participant]))` — now the attacker's key. [5](#0-4) [6](#0-5) 

Because `pop` on each `EncryptedMessage` proves possession of the *per-message* key only, it does not detect that the recipient-side `enc_key` was substituted. [7](#0-6) 

### Impact Explanation
An attacker who rewrites `enc_key` in victim `l`'s round-1 broadcast learns every secret share addressed to `l` (`share_i(l)` from all participants), which is exactly the information `l` itself would receive — the attacker reconstructs `l`'s `ThresholdKeys` secret share. Repeating the substitution for `t` distinct participant indexes yields `t` key shares and full recovery of the group secret key, after which the attacker can forge FROST signatures / spend multisig funds. This is "key share recovery" and "signing of unintended messages" reachable purely from tampering with public DKG message bytes — the same MitM-only requirement as the DIRAC report.

### Likelihood Explanation
Requires the ability to modify DKG round-1 messages in transit (the library explicitly does not handle networking/authenticity of the transport). This matches the DIRAC advisory's own note that such attacks require network-level MITM positioning, which limits but does not eliminate exploitability — grid-site/relay compromise, malicious coordinator infrastructure, or unauthenticated broadcast channels all suffice. Within the library's trust model, `enc_key` is the only field of the round-1 message lacking cryptographic binding, so any transport that authenticates sender identity per-participant but not message integrity (exactly the "checksum over the same channel" pattern) is exploitable.

### Recommendation
Bind `enc_key` into the signed transcript: include `enc_key.to_bytes()` in `Commitments`' `cached_msg` (or extend the PoK `challenge` to cover `enc_key`), so the Schnorr PoK authenticates the whole `EncryptionKeyMessage`. Optionally also require a PoP on `enc_key` itself to prevent registering keys whose discrete log is unknown.

### Proof of Concept
```rust
// Attacker sits on the broadcast channel for victim participant l.
// 1. Intercept l's EncryptionKeyMessage<Commitments> bytes.
//    Layout: [t * C::G commitment points][SchnorrSignature][enc_key: C::G]
// 2. Generate attacker scalar a; set A = G * a.
// 3. Overwrite the final C::G::Repr-sized bytes (enc_key) with A.to_bytes().
//    The embedded SchnorrSignature still verifies because challenge() is over
//    context || l || sig.R || cached_msg(commitment points) — enc_key is absent.
// 4. Forward the modified message. In verify_r1, batch_verify of msg.sig passes,
//    and Decryption::register stores A for participant l.
// 5. Every honest participant i calls encryption.encrypt(rng, l, share_i(l)),
//    deriving cipher key from ecdh(k_msg, A). Attacker computes the same key
//    via ecdh(a, msg.key = k_msg*G) and decrypts each EncryptedMessage<SecretShare>.
// 6. Attacker reconstructs l's secret share (sum of share_i(l) for PedPoP).
//    Repeat for t victims to recover the group secret key.
// Victim l additionally cannot decrypt their own shares (encrypted to A), so
// calculate_share fails and blame is issued — but the shares are already leaked.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L82-91)
```rust
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-154)
```rust
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());
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

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
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
  }
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
