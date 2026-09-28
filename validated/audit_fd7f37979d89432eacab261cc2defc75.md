One promising analog: `EncryptionKeyMessage.enc_key` is read via `C::read_G` and used directly for ECDH. If an unvalidated identity/torsion point is accepted, the ECDH shared key becomes public and the "encrypted" share is effectively plaintext. Check `read_G` identity handling.### Title
PedPoP DKG accepts identity point as encryption key, transmitting secret shares effectively in plaintext - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The bug class of the external report is *secret material transmitted in plaintext over a channel where an observer can read it*. In Serai's PedPoP DKG, each participant registers a long-term encryption public key (`enc_key`) in round 1 via `EncryptionKeyMessage`, and secret shares are "encrypted" to it in round 2 with an ECDH-derived ChaCha20 keystream. `EncryptionKeyMessage::read` parses `enc_key` with `C::read_G` where `C: Ciphersuite`, and `ciphersuite::Ciphersuite::read_G` only enforces canonical encoding — it does **not** reject the identity point (the identity rejection exists only in `frost::Curve::read_G`, a different trait pedpop does not use). A participant registering `enc_key = identity` forces every ECDH shared key computed for them to equal the identity point, making the ChaCha20 key a publicly computable deterministic value — the share ciphertext is plaintext-equivalent to any observer. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`EncryptionKeyMessage::read` reads `enc_key` via `C::read_G(reader)` (`crypto/dkg/pedpop/src/encryption.rs:57-58`), and `Decryption::register`/`Encryption::register` store it unconditionally (`encryption.rs:351-361`, `452-458`). `Ciphersuite::read_G` checks only canonicality (`ciphersuite/src/lib.rs:91-101`); the identity-rejecting variant is `Curve::read_G` (`frost/src/curve/mod.rs:125-131`), which is not in scope here since pedpop is generic over `ciphersuite::Ciphersuite`.

`encrypt` derives the shared secret as `ecdh(key, to) = to * key` (`encryption.rs:95-97`, `154`). If `to = identity`, the shared point is identity regardless of the fresh per-message scalar `key`. `cipher` then derives the ChaCha20 key solely from `context` and `ecdh.to_bytes()` (`encryption.rs:101-133`) — both public constants/observable values — and uses a fixed IV. Anyone who knows the context (a 32-byte protocol value, not secret) can recompute the keystream and decrypt `msg`.

The per-message Schnorr PoP (`pop_challenge`, `encryption.rs:302-324`) only binds the *sender's* ephemeral key; nothing authenticates or validates the *recipient's* registered `enc_key`. The spec (`spec/cryptography/Distributed Key Generation.md`) assumes an authenticated channel for messages but the ciphertext itself is the confidentiality mechanism — with `enc_key = identity` that mechanism is void.

### Impact Explanation
Any party that can observe the round-2 `EncryptedMessage<C, SecretShare<C::F>>` addressed to the malicious participant — including non-participant observers of the transport and any participant who receives relayed copies — recovers the secret share `f_sender(l)` for the malicious index `l` from every honest sender. This is secret key-share material delivered in effectively plaintext form, the direct analog of the Jenkins plugin transmitting passwords in cleartext form fields. Each leaked share is a Pedersen/Feldman share of an honest dealer's polynomial; combined with shares the attacker legitimately holds and any further leakage (e.g., the blame mechanism revealing ECDH keys for that participant's messages), it reduces the effective threshold an attacker must achieve and exposes honest dealers' shares to parties never meant to hold them.

### Likelihood Explanation
Requires one malicious (or compromised) DKG participant who registers `enc_key = identity`, plus an observer able to read that participant's incoming round-2 messages. The code path is fully reachable from public inputs: `EncryptionKeyMessage::read` → `Commitments::read` accepts the identity encoding without error, and honest senders' `generate_secret_shares` will happily encrypt to it (`lib.rs:366-369`). No collusion, broken BFT, or leaked keys required. Exploitation is silent — honest parties get no indication.

### Recommendation
Reject identity (and document rejection) when reading or registering the encryption key: in `EncryptionKeyMessage::read` or `Decryption::register`, check `enc_key.is_identity()` and error. Ideally require a proof of possession for `enc_key` at registration so participants prove they know its discrete log, aligning the recipient key with the sender-key PoP already enforced by `pop_challenge`.

### Proof of Concept
1. Attacker controls participant `l` in a `t`-of-`n` PedPoP DKG.
2. In round 1, attacker broadcasts `EncryptionKeyMessage { msg: valid Commitments, enc_key: C::G::identity() }`. `C::read_G` accepts the canonical identity encoding; `verify_r1`/`register` store it.
3. Each honest sender `s` calls `encrypt(rng, l, share_bytes)`, computing `ecdh = identity * key = identity`, `key = transcript(context || identity_bytes)`, fixed IV `b"DKG IV v0.2\0"`, and sends `EncryptedMessage { key: pub_key, pop, msg: ciphertext }`.
4. Observer reconstructs `cipher(context, &identity)` identically, XORs the keystream against `msg`, and recovers `SecretShare` bytes → `f_s(l)` for every honest dealer `s`.

Note: residual uncertainty — whether any outer Serai layer additionally authenticates/rejects identity `enc_key` was not fully verified; within the in-scope pedpop crate no such check exists.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

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

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
