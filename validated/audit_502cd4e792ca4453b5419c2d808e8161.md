### Title
Identity-point breakout via `Ciphersuite::read_G` in PedPoP `Commitments::read` bypasses the identity rejection enforced by `Curve::read_G` — (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The realms-shim breakout class is: attacker-controlled input reaches a more privileged/weaker primitive than the sandbox intended, escaping the restrictions of the intended context. Serai encodes exactly this boundary in two `read_G` functions: `Ciphersuite::read_G` (canonicality only) and `Curve::read_G` (canonicality + identity rejection, since the identity point is invalid for FROST/DKG material). `Commitments::read` in PedPoP — which consumes raw, untrusted broadcast bytes from other participants — deliberately reconstructs the raw buffer and calls `C::read_G` where `C: Ciphersuite`, i.e., the *unrestricted* primitive, thereby letting the identity element "break out" into a context (`ThresholdKeys`/PedPoP verification shares) that FROST explicitly designed to exclude it. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`Commitments::read` (crypto/dkg/pedpop/src/lib.rs:115–124) defines a local `read_G` closure that reads a `GroupEncoding` buffer and calls `C::read_G` — the `Ciphersuite` trait implementation, which accepts any canonical point, including the identity (`crypto/ciphersuite/src/lib.rs:91–101`). The codebase has a separate `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125–131`) whose sole added check — "identity point" rejection — exists because the identity is invalid for verification shares and commitments in this protocol family. PedPoP `Commitments` are the polynomial commitments `A_j = f_j * G` broadcast by each participant and later used to verify secret shares (`share * G == Σ l^j * A_j`).

Because the PedPoP `Commitments` type is generic over `Ciphersuite` rather than `Curve`, the intended sandbox boundary (non-identity group elements only) is not enforced on this attacker-supplied input. An unprivileged participant can broadcast `Commitments` containing `t` encodings of the identity point plus a trivially-forged PoK `SchnorrSignature` (with `A = identity`, any `(R = s*G, s)` satisfies `s*G == R + c*A`; the `challenge` function at lib.rs:86–94 binds no restriction preventing this).

The result is a degenerate zero polynomial accepted by all honest participants: the attacker's `A_0` contributes the identity to the group key, and every share the attacker must distribute is `0`, which verifies cleanly against identity commitments. The attacker obtains a fully valid `ThresholdKeys` share while contributing zero entropy to the resulting group key and while their polynomial is publicly degenerate — a violation of PedPoP's core guarantee (each participant contributes an unpredictable secret to the group key and each share binds to a committed polynomial), achievable only because the deserialization path escaped to the weaker read primitive.

### Impact Explanation
Any remote party able to send PedPoP `Commitments` bytes (the intended broadcast round of the DKG) can inject identity points into verification-share material that every downstream consumer treats as valid non-identity curve points. Concretely: a malicious participant can commit to the zero polynomial, distribute zero shares that pass per-share verification, and complete the DKG holding a valid share of a group key to which they contributed no secret. In configurations where entropy contribution matters (e.g., a participant expected to blind the key against the coordinator/others), the guarantee is silently void. Since `Commitments::read` also powers `EncryptionKeyMessage::read` (encryption.rs:57–59), the same escape applies to the encryption-key field path via `C::read_G` at line 58, where an identity `enc_key` causes ECDH to produce the identity shared secret — a publicly known cipher key, leaking the encrypted share to any observer who can compute `cipher(context, identity)`. That second consequence is the stronger one: an attacker submitting identity `enc_key` trivially knows the "shared" secret used to encrypt their own incoming shares, and — more importantly — an honest sender encrypting *to* a malicious participant's identity `enc_key` uses a publicly derivable ChaCha20 key (static IV `DKG IV v0.2\0`), so the message is plaintext-readable to anyone recording the wire.

### Likelihood Explanation
Reachability is direct: `Commitments::read` and `EncryptionKeyMessage::read` are the deserialization entry points for the two broadcast/peer messages of the PedPoP DKG, fed entirely by attacker-controlled bytes. No special positioning is required — any DKG participant can send identity encodings. The only mitigating factor is that real-world impact on group-key secrecy requires the attacker to be a participant (which is within the threat model; PedPoP explicitly handles faulty/malicious participants) and that the zero-entropy-contribution attack is most damaging in small-`n` or adversary-heavy sets; the identity `enc_key` → public ECDH secret issue, however, is unconditional for any participant who submits it.

### Recommendation
In `Commitments::read` (crypto/dkg/pedpop/src/lib.rs:115–121) and `EncryptionKeyMessage::read` (crypto/dkg/pedpop/src/encryption.rs:58), reject identity points after `C::read_G`, mirroring `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125–131`):

```rust
let point = C::read_G(&mut buf.as_ref())?;
if point.is_identity().into() {
  Err(io::Error::other("identity point"))?;
}
```

Alternatively, bound PedPoP generics by a trait exposing the identity-rejecting reader so the stronger primitive cannot be bypassed by construction.

### Proof of Concept
```rust
// For any C: Ciphersuite used with PedPoP (e.g., dalek_ff_group::Ed25519):
// 1. Build Commitments { commitments: [identity; t], sig } where sig is a
//    Schnorr signature over the identity "key":
//    pick s = C::F::random(), R = C::generator() * s, c = challenge(...) with
//    Am = cached_msg of t identity encodings; s satisfies s*G == R + c*identity.
// 2. write() the Commitments; victim calls Commitments::read(reader, params).
//    - Ciphersuite::read_G accepts the canonical identity encoding.
//    - Curve::read_G would have rejected it.
// 3. Victim verifies the PoK (passes, A = identity) and later verifies the
//    attacker's zero share: 0*G == sum(l^j * identity) == identity. Accepted.
// 4. For the enc_key path: submit EncryptionKeyMessage with enc_key = identity.
//    Any encrypt-to-self/other flow deriving ecdh(priv, identity) = identity
//    produces a publicly known ChaCha20 key (cipher(context, identity)),
//    decrypting that participant's DKG share message for any observer.
```

Key uncertainty: whether the coordinator-layer callers pre-validate commitments before `Commitments::read` (outside the in-scope crates), which would lower practical severity; the cryptographic flaw in the in-scope deserialization path stands regardless.

### Citations

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
