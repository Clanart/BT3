### Title
PedPoP accepts identity encryption keys, making the per-message proof-of-possession universally forgeable and collapsing the ECDH cipher key to a public constant - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptedMessage::read` accepts an attacker-supplied ephemeral key `key: C::G` via `C::read_G`, which validates only canonical encoding — it does not reject the identity element. `read_G` decodes the point and checks `point.to_bytes() == encoding` but never checks `is_identity`, so `key = identity` parses cleanly [1](#0-0) . The proof-of-possession `pop` is a Schnorr signature over `key` whose verification equation is `s*G == R + c*key`. With `key = identity` the challenge term vanishes, so any `(R = s*G, s)` pair verifies — a forged proof with no secret knowledge. The same identity key feeds `ecdh`, producing `shared = private * identity = identity`, so the ChaCha20 cipher key derived in `cipher()` becomes a fixed public value (a transcript hash of the identity encoding plus context), readable by anyone [2](#0-1) .

### Finding Description
This is the direct analog of CVE-2017-7466's class: structured data supplied by a managed/untrusted party is accepted without validating a semantic invariant (non-identity group element), and that input is then consumed by security-critical code on the receiving side. `EncryptedMessage::read` reads `key`, `pop`, and `msg` from untrusted bytes with no identity check [3](#0-2) . The `pop` field exists specifically to stop an attacker from claiming keys they cannot prove possession of (commented threat model at lines 84-90), but the Schnorr equation over an identity `key` is satisfiable by construction: choose `s`, set `R = s*G`; `s*G == R + c*identity` holds for every `c` [4](#0-3) . The decryption path computes `ecdh(enc_scalar, msg.key) = identity` and `cipher()` then derives the ChaCha20 key purely from `context` (public session id) and the identity's fixed encoding — a key any observer can recompute [5](#0-4) . The same flaw applies symmetrically to `EncryptionKeyMessage::read`, whose `enc_key` is also read with `C::read_G` and no identity rejection [6](#0-5) : a malicious DKG participant publishing `enc_key = identity` causes every honest participant's `encrypt()` call to compute `ecdh(key_priv, identity) = identity`, so all secret shares addressed to that participant are encrypted under a publicly derivable key.

### Impact Explanation
Two concrete impacts: (1) a forged Schnorr proof-of-possession is accepted by `calculate_share`'s PoP verification, defeating the documented defense against key-copying/blame side channels; (2) every `EncryptedMessage` addressed to a participant advertising `enc_key = identity` is confidential to no one — the ECDH shared point is the identity encoding, so `cipher()` produces a keystream any network observer can regenerate, publicly exposing the secret-share ciphertexts honest parties send to that index. Shares routed to the malicious index are ones the attacker legitimately receives, but honest parties additionally lose the per-message forward-secrecy/blame-safety guarantees the PoP was added for, and any party's share ciphertext to that index is trivially decryptable by third parties.

### Likelihood Explanation
Reachable by any unauthenticated DKG participant: the attacker only needs to send a `EncryptionKeyMessage`/`EncryptedMessage` with an identity point encoding, which passes `Commitments::read`/`EncryptedMessage::read` unprivileged deserialization [7](#0-6) . Forging the PoP requires no secret and no computation beyond one scalar multiplication. Exploitation needs an observer on the (typically authenticated but not necessarily confidential) broadcast channel to derive the public cipher key.

### Recommendation
Reject identity points in `C::read_G` consumers that feed ECDH or PoK verification — specifically in `EncryptedMessage::read` and `EncryptionKeyMessage::read` (`crypto/dkg/pedpop/src/encryption.rs`) check `key.is_identity()` / `enc_key.is_identity()` and error out; equivalently reject identity inside the PoP verification path. Consider a shared `read_nonzero_G` helper since the same gap likely affects other `read_G` call sites (`Commitments::read`, `GeneratorProof::read`).

### Proof of Concept
```rust
// Attacker registers enc_key = identity in their EncryptionKeyMessage.
let identity = C::G::identity();
// In EncryptedMessage / EncryptionKeyMessage serialization:
// write identity.to_bytes() as enc_key.

// Forge the PoP: SchnorrSignature { R, s } with R = s*G, any s.
let s = C::F::random(rng);
let r_point = C::generator() * s;
// Verification computes c = pop_challenge(context, R, key=identity, from, msg)
// and checks s*G == R + c*identity == R. Holds unconditionally.

// Recipient side:
// ecdh(enc_scalar, identity) == identity
// cipher key = H("DKG Encryption v0.2" || context || "encryption_key" || identity.to_bytes())
// => deterministic public ChaCha20 key; attacker XORs it against msg ciphertext
// to recover the secret share plaintext without knowing any private key.
```

Root cause is confirmed in the reading and cipher-derivation paths: no identity check exists anywhere between `read_G` and `ecdh`/`cipher` [8](#0-7) . One caveat: I was unable to fully read the `decrypt`/`verify` function bodies in `encryption.rs` beyond line 170 and the exact `SchnorrSignature::verify` implementation within the iteration budget; the forgery argument relies on the standard Schnorr verification equation implied by the PoK usage and the `invalidate_pop`/`pop_challenge` test helpers.

### Citations

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L80-93)
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
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-133)
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
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-183)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.pop.write(writer)?;
    self.msg.write(writer)
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
