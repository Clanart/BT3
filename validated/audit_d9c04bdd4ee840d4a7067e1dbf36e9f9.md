### Title
Missing validation of the per-message encryption key lets an unprivileged party forge an authenticated `EncryptedMessage` and maliciously attribute a fault to any participant - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2025-46175 is a missing permission check (`checkUserDataScope`) that lets an unauthenticated user read data outside their authorized scope. The Serai analog is a missing cryptographic validation check: `EncryptedMessage::read` accepts a per-message ephemeral key of `identity`, for which the Schnorr proof-of-possession — the *only* access/authenticity control on the message — is trivially satisfiable by anyone (the discrete log of the identity point is `0`). A forged message also decrypts under a publicly computable cipher key, giving the attacker complete control of the plaintext while the message passes every authentication gate in `KeyMachine::calculate_share` and `BlameMachine::blame`.

### Finding Description
`EncryptedMessage::read` deserializes `key` via `C::read_G` (the `Ciphersuite` trait), which accepts the identity point — there is no non-identity check anywhere in `crypto/dkg/pedpop`: [1](#0-0) 

Contrast with `frost::curve::Curve::read_G`, which *does* reject identity — proving the authors consider identity rejection necessary but never applied it to the DKG crate's `Ciphersuite::read_G` path: [2](#0-1) 

The PoP is a `SchnorrSignature` verified as `s·G == R + c·P` via `multiexp_vartime`, with no identity checks on `R` or `P`: [3](#0-2) 

With `msg.key = P = identity` (discrete log `0`), anyone produces a valid PoP: pick `r`, set `R = r·G`, `s = r`. The signature verifies for *any* `from` and *any* ciphertext, because `pop_challenge` binds `from`, `R`, `key`, and `msg`, all attacker-chosen: [4](#0-3) 

Furthermore, the ECDH shared point is `enc_key_recipient * msg.key = identity`, so the ChaCha20 cipher key is `H(context, identity)` — publicly derivable. The attacker fully controls the decrypted plaintext share: [5](#0-4) [6](#0-5) 

`blame_internal` / `decrypt_with_proof` take the `sender` index as an argument and evaluate attacker-supplied `msg` bytes; a forged message with a valid PoP and a valid `EncryptionKeyProof` (for `msg.key = identity`, the shared key is `identity` and the DLEq is provable with the decryptor's own key) yields a decrypted share that necessarily fails verification against `commitments[&sender]` — so the function returns `sender`, i.e., the *accused* party is declared faulty: [7](#0-6) [8](#0-7) 

### Impact Explanation
The blame system exists so that an accusation cryptographically identifies the faulty party — it is Serai's "access control" over who may be marked malicious. With `msg.key = identity`:

1. An accuser crafts an `EncryptedMessage` for arbitrary `sender = victim`, `msg` = attacker-chosen bytes decrypting to an invalid share (or non-canonical scalar).
2. The PoP verifies because the discrete log of `identity` is known (`0`), so `decrypt_with_proof` does not return `DecryptionError::InvalidSignature`.
3. The accuser supplies a valid `EncryptionKeyProof` for the identity shared point (provable with their own encryption secret).
4. `blame_internal` decrypts, sees a share inconsistent with `commitments[&victim]`, and returns `victim` as the faulty party — a blame verdict any observer (including `AdditionalBlameMachine::new`, explicitly usable by non-participants) can independently verify.

The result is a forged, universally verifiable fault attribution against an honest participant. Downstream, Serai treats verified `InvalidDkgShare`/`InvalidShare` blame as grounds for removing/slashing the accused validator and aborting the key generation session. Even where transport authenticates `sender`, `calculate_share` additionally trusts the PoP + ECDH path (batch `BatchId::Decryption`/`BatchId::Share` queues), so the missing identity check corrupts the share-aggregation path itself: `self.secret += share` integrates attacker-chosen plaintext before verification fails, and the error is attributed to the forged `from` index rather than flagged as a malformed message.

### Likelihood Explanation
- Reachability: `EncryptedMessage::read` is fed untrusted bytes (`shares` map in `calculate_share`, `share`/`blame` bytes in `VerifyBlame` handling). Constructing the forgery requires only public data (the context, the victim's participant index, the victim's registered `enc_key`) — no discrete logs beyond `identity`'s known `0`.
- Prerequisites: the attacker participates in (or can submit blame/share data into) a DKG session. No collusion, threshold corruption, or leaked keys needed.
- Constraint: `C::read_G` for the in-scope ciphersuites (dalek-ff-group `Ristretto`/`Ed25519`, kp256 `Secp256k1`) decompresses canonical encodings without an identity rejection — identity rejection exists only in `frost::curve::Curve::read_G`, which PedPoP does not use. This is consistent with `Signed::read` performing its own `is_identity` check, confirming `Ristretto::read_G` accepts identity.

One residual uncertainty: whether the surrounding processor/coordinator layer re-verifies that the accused-share bytes match what was originally broadcast under the accused's signed transaction (the `blame` doc comment says the message "must have been authenticated"). If that check exists, the forgery still defeats the *in-protocol* authenticity gate (the PoP), and `AdditionalBlameMachine` — which is documented for use by external verifiers who only have commitment messages — remains exploitable.

### Recommendation
- In `EncryptedMessage::read` (and `EncryptionKeyMessage::read` for `enc_key`), reject identity keys — e.g., `if msg.key.is_identity().into() { Err(io::Error::other("identity encryption key")) }` — mirroring `Curve::read_G`'s existing check.
- Additionally reject `R`/`public_key` identity inside `SchnorrSignature::verify`/`batch_statements` consumers in the DKG path, or assert `msg.key` non-identity before queueing `pop.batch_verify` in `Encryption::decrypt` and `Decryption::decrypt_with_proof`.
- Consider binding the recipient index into `pop_challenge` so a message is bound to `from → to`, not just `from`.

### Proof of Concept
```rust
// Context: PedPoP DKG over Ristretto, attacker is a participant (or an accuser
// invoking BlameMachine::blame / AdditionalBlameMachine::blame).
// victim: Participant whose fault we want to forge.

use dalek_ff_group::{Ristretto, EdwardsPoint, Scalar};
use ciphersuite::{group::{ff::Field, Group, GroupEncoding}, Ciphersuite};
use schnorr::SchnorrSignature;

// 1. Forge an EncryptedMessage attributed to `victim`:
let key = EdwardsPoint::identity();           // msg.key = identity, dlog = 0
let r = Scalar::random(&mut OsRng);
let R = <Ristretto as Ciphersuite>::generator() * r;
// Attacker-chosen plaintext (e.g., a scalar that parses but is inconsistent
// with victim's commitments):
let plaintext = Scalar::ONE.to_repr();
// Cipher key is PUBLIC: ecdh = enc_key_victim * identity = identity
let mut msg_bytes = SecretShare(plaintext);
cipher::<Ristretto>(context, &Zeroizing::new(EdwardsPoint::identity()))
    .apply_keystream(msg_bytes.0.as_mut());
// PoP for pubkey = identity is trivially satisfiable:
let pop = SchnorrSignature::<Ristretto>::sign(
    &Zeroizing::new(Scalar::ZERO), // "private key" 0 for identity
    Zeroizing::new(r),
    pop_challenge::<Ristretto>(context, R, key, victim, msg_bytes.0.as_ref()),
);
let forged = EncryptedMessage { key, pop, msg: Zeroizing::new(msg_bytes) };

// 2. As accuser, produce the EncryptionKeyProof for the identity shared point:
//    DLEq over [G -> our_enc_pub], [identity -> identity] with our own enc secret.
//    Both blame machines then return `victim` as the faulty participant:
//    - pop.verify passes (valid sig under identity key)
//    - proof.dleq verifies (real proof, shared point = identity)
//    - decrypted share fails share_verification_statements vs commitments[victim]
//    => blame_internal returns `victim`
```

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
```rust
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-488)
```rust
    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
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

**File:** crypto/schnorr/src/lib.rs (L108-110)
```rust
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L582-605)
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
```
