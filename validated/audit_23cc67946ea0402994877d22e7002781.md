### Title
Identity group element accepted by `Ciphersuite::read_G` enables forged proof-of-possession and blame-framing of honest DKG participants - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report's bug class is "accepting untrusted serialized input without constraining it to valid/safe values." In Serai, `Ciphersuite::read_G` in `crypto/ciphersuite/src/lib.rs` canonicalizes and torsion-checks incoming point encodings, but deliberately does **not** reject the identity element — that check exists only in `frost::Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`). Every PedPoP/encryption-layer reader (`EncryptionKeyMessage::read`, `EncryptedMessage::read`, `EncryptionKeyProof::read`, `GeneratorProof::read`) is generic over `Ciphersuite`, not `Curve`, so the identity point flows in as a valid public key for the Schnorr proof-of-possession (`pop`) on every `EncryptedMessage`. A Schnorr signature under public key = identity is universally forgeable: the verification equation `R + c·A − s·G = 0` reduces to `R = s·G` for any `s`, regardless of the challenge. This lets any party craft an `EncryptedMessage` that passes the PoP check while claiming an arbitrary honest `sender`, which then feeds the blame/slashing path (`Decryption::decrypt_with_proof` → `AdditionalBlameMachine::blame` → `CoordinatorMessage::VerifyBlame`).

### Finding Description
`EncryptedMessage` carries `key: C::G` and `pop: SchnorrSignature<C>` where `pop` proves possession of `key`'s discrete log, bound to `context`, `sender`, and the ciphertext (`pop_challenge`, `crypto/dkg/pedpop/src/encryption.rs:302-324`). The comment at `encryption.rs:83-90` states the PoP exists so Eve cannot replay/forge a message under someone else's encryption key — but that guarantee silently assumes `key` is a real public key.

- `EncryptedMessage::read` (encryption.rs:171-177) reads `key` via `C::read_G`, which for `C: Ciphersuite` resolves to `Ciphersuite::read_G` (`ciphersuite/src/lib.rs:91-101`) — canonical and torsion-free, but identity-permitting.
- `Decryption::decrypt` (encryption.rs:469-501) batch-verifies `pop` against `msg.key`, computes `ecdh = enc_key_priv * msg.key`, and decrypts.
- `Decryption::decrypt_with_proof` (encryption.rs:366-397), used by the blame flow (`processor/src/key_gen.rs:504-549`), verifies `pop` against `msg.key` and then a DLEq that the revealed `proof.key` equals `enc_key_priv * msg.key`.

With `msg.key = identity`:
1. Forged PoP: pick `s`, set `R = s·G`, compute `c = pop_challenge(context, R, identity, victim_sender, msg)`; verification holds since `c·identity = identity`.
2. Forged decryption proof: any honest accuser computes `proof.key = enc_key_priv * identity = identity`. The DLEq over generators `[G, identity]` is provable because the second statement `s·identity − c·identity = identity` constrains nothing — the prover only needs the discrete log of their own `enc_pub_key`, which they know.
3. The garbage ciphertext "decrypts" to a garbage `SecretShare`, which fails share verification against `victim_sender`'s commitments — so `victim_sender` is adjudicated faulty (`ProcessorMessage::Blame` / `InvalidShare`).

### Impact Explanation
An unprivileged party can fabricate an `EncryptedMessage<SecretShare>` addressed to any DKG participant, naming any honest participant as `from`. The recipient's `KeyMachine::calculate_shares`/blame flow and the coordinator-side `VerifyBlame` handler (`processor/src/key_gen.rs:504-549`) both accept the forged PoP and the accuser's (legitimately derivable) decryption proof, conclude the share is invalid, and attribute the fault to the framed participant. In Serai's validator-set design, a confirmed `Blame`/`InvalidShare` leads to slashing of the accused validator's stake — i.e., an honest participant loses funds and is ejected from the set — or at minimum aborts the DKG session. This is exactly the class the external report describes: deserialization that fails to reject a degenerate value, producing a security-relevant object (here, a "valid" proof) from untrusted bytes.

### Likelihood Explanation
Fully deterministic: the forgery requires no discrete logs, no race, and no collusion — any party able to deliver a share message to a DKG participant (the DKG runs over authenticated but not confidential channels per the design comments) can trigger it once per target. The only mitigations are behavioral (channel-layer deduplication blaming the actual sender) which the library explicitly disclaims. Reachability is confirmed: `EncryptedMessage::read` is called directly on untrusted bytes in `processor/src/key_gen.rs:508-519` and in the round-2 share processing path.

### Recommendation
Reject the identity point in the PedPoP/encryption deserialization and verification paths. Either add an `is_identity` check inside `EncryptedMessage::read`/`EncryptionKeyMessage::read`/`EncryptionKeyProof::read` (mirroring `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`), or verify `pop` only after asserting `msg.key` is non-identity in `Decryption::decrypt` and `decrypt_with_proof`. For defense in depth, also reject identity `enc_key` in `Decryption::register` (an identity registered key makes the ECDH output the publicly-known identity point, nullifying share confidentiality).

### Proof of Concept
```rust
// Forged EncryptedMessage claiming to be from honest participant `victim`, addressed to `target`
// C: any in-scope ciphersuite; identity = C::G::identity()

// 1. Forge the PoP under public key = identity
let s = C::F::random(&mut OsRng);            // arbitrary
let R = C::generator() * s;                  // R = s*G
let garbage_share = SecretShare::<C::F>([0x41; _]);  // any bytes
let c = pop_challenge::<C>(context, R, C::G::identity(), victim, garbage_share.as_ref());
// SchnorrSignature { R, s } satisfies R + c*identity - s*G == identity
let forged = EncryptedMessage { key: C::G::identity(), pop: SchnorrSignature { R, s }, msg: garbage_share };

// 2. Deliver `forged.serialize()` to `target` as victim's share message.
//    - EncryptedMessage::read accepts key = identity (Ciphersuite::read_G, lib.rs:91-101)
//    - decrypt() batch-verifies pop: passes (equation degenerates to R == s*G)
//    - share verification against victim's commitments fails
//    - target issues ProcessorMessage::InvalidShare / Blame against `victim`

// 3. Coordinator-side VerifyBlame (key_gen.rs:504-549):
//    - decrypt_with_proof re-verifies pop: passes
//    - accuser's EncryptionKeyProof: key = enc_priv * identity = identity;
//      DLEq over [G, identity] proves cleanly since the identity statement is vacuous
//    - decrypted bytes are garbage -> victim confirmed faulty -> slashed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-100)
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
```

**File:** processor/src/key_gen.rs (L504-522)
```rust
      CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame } => {
        let params = ParamsDb::get(txn, &id.session, id.attempt).unwrap().0;

        let mut share_ref = share.as_slice();
        let Ok(substrate_share) = EncryptedMessage::<
          Ristretto,
          SecretShare<<Ristretto as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        let Ok(network_share) = EncryptedMessage::<
          N::Curve,
          SecretShare<<N::Curve as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        if !share_ref.is_empty() {
          return ProcessorMessage::Blame { id, participant: accused };
        }
```
