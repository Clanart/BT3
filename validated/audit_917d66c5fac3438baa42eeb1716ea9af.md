### Title
Identity points accepted in PedPoP DKG encryption/commitment paths while FROST rejects them — inconsistent point-validation policy lets DKG secret shares be encrypted to a publicly known key and PoKs be forged - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The OpenClaw advisory is about a shared sanitization policy being applied inconsistently across paths. Serai has the same shape: `Curve::read_G` (used by FROST's preprocess/`Commitments` readers) fail-closed rejects the identity point, while the PedPoP DKG — generic only over `Ciphersuite` — reads all points through `Ciphersuite::read_G`, which performs canonical-encoding checks but accepts identity. The identity point therefore slips through every PedPoP deserialization path: `EncryptionKeyMessage.enc_key`, `EncryptedMessage.key`, `Commitments.commitments[0]` (the PoK public key), and `EncryptionKeyProof.key`.

### Finding Description
`crypto/frost/src/curve/mod.rs:125-131` defines `Curve::read_G`, which calls `Ciphersuite::read_G` and then rejects identity:

```rust
let res = <Self as Ciphersuite>::read_G(reader)?;
if res.is_identity().into() {
  Err(io::Error::other("identity point"))?;
}
```

FROST preprocesses route through this (`crypto/frost/src/nonce.rs:34-35`). PedPoP, however, is bound by `C: Ciphersuite`, so all of its readers use the base `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`), which only enforces canonical encoding:

- `EncryptionKeyMessage::read` reads `enc_key` with `C::read_G` (`crypto/dkg/pedpop/src/encryption.rs:57-59`). This key is stored in `Decryption.enc_keys` (`encryption.rs:360`) and later used as the ECDH peer key in `encrypt` → `ecdh(key, to)` (`encryption.rs:154`, `:95-97`). If a participant registers `enc_key = identity`, then `ecdh(·, identity) = identity` for every sender, so the ChaCha20 keystream key is derived by `cipher()` (`encryption.rs:101-133`) from a publicly computable transcript input (`context || identity.to_bytes()`). Every `SecretShare` encrypted to that participant is decryptable by anyone who observes the ciphertext — the DKG's share confidentiality is silently void, and the recipient's resulting private key share becomes recoverable by any observer.
- `EncryptedMessage::read` reads `key` with `C::read_G` (`encryption.rs:171-177`). A sender can set `key = identity`, making `ecdh(recipient_enc_key_priv, identity) = identity`; the PoP `SchnorrSignature::verify(identity, c)` reduces to `R == sG` and is trivially satisfiable (`crypto/schnorr/src/lib.rs:88-99`, `verify` at `:108-110`), so the message passes the PoP gate in `decrypt`/`decrypt_with_proof` (`encryption.rs:374-379`) while being decryptable under a public key.
- `Commitments::read` (`crypto/dkg/pedpop/src/lib.rs:109-128`) reads `commitments[0..t]` via `C::read_G`. In `verify_r1` (`lib.rs:323-329`), the proof-of-knowledge is batch-verified with `commitments[0]` as the public key. With `commitments[0] = identity`, the Schnorr batch statement becomes `R + c·identity − sG = 0`, i.e. `R = sG`, which any forger satisfies by picking `s` and setting `R = sG` — a forged proof of knowledge accepted without knowing any discrete log.

### Impact Explanation
- An unprivileged participant can register `enc_key = identity` (or a peer can send `EncryptedMessage.key = identity`); all secret shares subsequently encrypted under that registration use an ECDH output of identity, derivable by anyone, so the victim's DKG secret share is exposed to any observer of the share ciphertexts — private key share recovery.
- The PedPoP proof of knowledge on the polynomial's constant term is forgeable via `commitments[0] = identity`, defeating the PoK's purpose (binding the participant to a known discrete log) and producing a forged proof the verifier accepts.

### Likelihood Explanation
Any DKG participant can set these fields — they are attacker-controlled bytes read via `EncryptionKeyMessage::read`/`EncryptedMessage::read`/`Commitments::read` from messages participants exchange. No collusion, no trusted-position required; the sender/registrant chooses the identity encoding directly.

### Recommendation
Apply the same fail-closed policy FROST uses: after `C::read_G`, reject `identity()` in `EncryptionKeyMessage::read`, `EncryptedMessage::read`, `EncryptionKeyProof::read`, and `Commitments::read` (at minimum `commitments[0]` and `enc_key`/`key`). Alternatively, have `cipher()`/`ecdh()` assert the ECDH output is non-identity, and reject identity public keys in `SchnorrSignature::verify`/`batch_statements` consumers that rely on the key committing to a secret.

### Proof of Concept
```rust
// Participant l registers an identity encryption key.
let mut msg_bytes = commitments.serialize(); // Commitments<C> bytes
msg_bytes.extend(C::G::identity().to_bytes().as_ref()); // enc_key = identity
let ekm = EncryptionKeyMessage::<C, Commitments<C>>::read(
  &mut msg_bytes.as_slice(), params).unwrap(); // ACCEPTED — no identity check

// Every sender now computes:
//   ecdh(their_ephemeral_key, identity) = identity
//   cipher key = H(context || "encryption_key" || identity.to_bytes()) — public
// => SecretShare<F> ciphertexts to l are decryptable by any observer.

// PoK forgery:
let mut com = vec![C::G::identity()]; // commitments[0] = identity
com.extend((1..t).map(|_| C::generator() * C::random_nonzero_F(rng)));
let s = C::random_nonzero_F(rng);
let forged_sig = SchnorrSignature::<C> { R: C::generator() * s, s };
// verify(identity, c): R + c*identity - sG = R - sG = 0 → passes batch_verify
// in verify_r1 (pedpop/src/lib.rs:323-334) without knowing any discrete log.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-60)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
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

**File:** crypto/dkg/pedpop/src/lib.rs (L322-334)
```rust
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** crypto/schnorr/src/lib.rs (L88-99)
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
```
