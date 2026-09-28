### Title
Missing proof-of-possession on DKG encryption keys lets a participant read another participant's secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
In PedPoP's DKG, each participant registers an encryption public key (`enc_key`) inside the round-1 `EncryptionKeyMessage`. Unlike the per-message keys, the registered `enc_key` carries **no proof of possession** — it is a bare group element the registrant asserts. A malicious participant Eve can register Alice's public encryption key (copied from Alice's broadcast commitments message, a public input) as her own. Every dealer then encrypts Eve's secret share to a key whose private part only Alice knows, granting Alice — an unintended party — read access to Eve's secret share. This mirrors CVE-2020-26212's bug class: a missing authorization/binding check lets one party read data intended for another.

### Finding Description
`EncryptionKeyMessage` wraps the round-1 payload together with `enc_key`, and `Decryption::register` inserts it unconditionally — no signature or PoK binds `enc_key` to `participant`:

```rust
// crypto/dkg/pedpop/src/encryption.rs
pub struct EncryptionKeyMessage<C: Ciphersuite, M: Message> {
  msg: M,
  enc_key: C::G,   // bare public key, no proof of possession
}
...
self.enc_keys.insert(participant, msg.enc_key);
``` [1](#0-0) [2](#0-1) 

The only PoP in the scheme is the per-message Schnorr signature inside `EncryptedMessage`, which binds the *ephemeral* encryption key — not the long-term `enc_key` (comment at lines 83–91 explicitly scopes the PoP to the per-message key). The commitments PoK (`challenge`/`SchnorrSignature::sign` over `coefficients[0]`) proves knowledge of the polynomial's constant term, not the encryption key:

```rust
let sig = SchnorrSignature::<C>::sign(&coefficients[0], r, challenge::<C>(...));
``` [3](#0-2) 

Encryption then ECDHs a fresh per-message key against whatever `enc_key` was registered for the recipient:

```rust
fn encrypt(...) -> EncryptedMessage<C, E> {
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());
``` [4](#0-3) [5](#0-4) 

Nothing in `verify_r1` or `register` checks that `enc_key` differs across participants or that its discrete log is known to the registrant.

### Impact Explanation
Eve registers `enc_key = Alice.enc_pub_key`. Every honest dealer encrypts Eve's share via `ecdh(ephemeral, Alice.enc_pub_key)` (`crypto/dkg/pedpop/src/lib.rs:369`). Alice, knowing the private key, computes `ecdh(alice_enc_key, msg.key)` and decrypts with `cipher(context, ...)` — recovering Eve's secret share `f_j(eve)` from every dealer `j`. This is disclosure of a secret share to a party who was never meant to possess it: a confidentiality/authorization failure reachable entirely with public inputs (Alice's broadcast commitments message). While a single leaked share does not by itself recover the group key, it weakens the threshold guarantee and leaks honest-party secret material. Additionally, Eve herself cannot decrypt her own shares, so her `calculate_share` fails and she issues blame proofs computed with the *wrong* ECDH key — those proofs, when adjudicated via `decrypt_with_proof`/`AdditionalBlameMachine::blame`, cause churn in the blame protocol before Eve is (correctly) blamed.

### Likelihood Explanation
The attack requires only that a DKG participant copy another participant's published `enc_key` into their own `EncryptionKeyMessage` — all inputs are broadcast public data. No collusion, no breaking of primitives. It is deterministic: every share addressed to Eve is ciphertext under Alice's key. Detection requires an external mechanism (key uniqueness checks across messages), which the code does not perform; `validate_map` only checks participant-index coverage, not key distinctness.

### Recommendation
Require a proof of possession of `enc_key` at registration: e.g., include a `SchnorrSignature` over `(context, participant, enc_key)` in `EncryptionKeyMessage`, verified inside `Decryption::register`/`verify_r1` (batch-verifiable alongside the coefficient PoKs in `verify_r1`). Alternatively, derive `enc_key` deterministically from the already-PoK'd `commitments[0]` via transcript, so key ownership is inherited from the existing proof. Also reject duplicate `enc_key` values across participants as a defense-in-depth check.

### Proof of Concept
1. Alice (participant `a`) runs `generate_coefficients`, broadcasting `EncryptionKeyMessage{msg: Commitments, enc_key: A}` where `A = G·a_enc`.
2. Eve (participant `e`) broadcasts `EncryptionKeyMessage{msg: Commitments_e, enc_key: A}` — a verbatim copy of Alice's `enc_key`. `Decryption::register` accepts it; no PoP is checked.
3. Every dealer `j` computes `share_j(e)` and calls `encrypt(rng, e, share)`, producing ciphertext under `ecdh(k_j, A)`.
4. Alice observes the `EncryptedMessage` addressed to Eve, computes `ecdh(a_enc, msg.key)`, runs `cipher(context, ...)`, and recovers `share_j(e)` — Eve's secret share — for all `j`.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L49-58)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-155)
```rust
  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
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

**File:** crypto/dkg/pedpop/src/lib.rs (L174-191)
```rust
    let r = Zeroizing::new(C::random_nonzero_F(rng));
    let nonce = C::generator() * r.deref();
    let sig = SchnorrSignature::<C>::sign(
      &coefficients[0],
      // This could be deterministic as the PoK is a singleton never opened up to cooperative
      // discussion
      // There's no reason to spend the time and effort to make this deterministic besides a
      // general obsession with canonicity and determinism though
      r,
      challenge::<C>(self.context, self.params.i(), nonce.to_bytes().as_ref(), &cached_msg),
    );

    // Additionally create an encryption mechanism to protect the secret shares
    let encryption = Encryption::new(self.context, self.params.i(), rng);

    // Step 4: Broadcast
    let msg =
      encryption.registration(Commitments { commitments: commitments.clone(), cached_msg, sig });
```
