### Title
Ed448 deserializes non-prime-order points, enabling Schnorr proof forgery - (File: crypto/ed448/src/point.rs)

### Summary
`Point::from_bytes` intends to reject points outside the prime-order subgroup, but `is_torsion_free` computes `(-P) + P` and checks whether the result is identity. That expression is identity for every valid Ed448 point, so low-order/torsion components are accepted during deserialization.

Because `Ciphersuite::read_G` only requires `from_bytes` to succeed and the encoding to round-trip, attacker-controlled bytes containing a canonical non-prime-order Ed448 point are accepted wherever `Ed448::read_G` is used.

### Finding Description
The subgroup check is implemented as: [1](#0-0) 

`Scalar::ZERO - Scalar::ONE` is `-1`, so this checks `(-P) + P == identity`, which is true for every group element. It therefore does not test whether `P` has order `l`, the Ed448 prime subgroup order.

The generic deserialization path accepts any point whose `from_bytes` result is successful and whose serialization is canonical: [2](#0-1) 

This malformed point reaches public-input parsers such as `EncryptedMessage::read`, which deserializes both the claimed encryption key and its Schnorr proof from untrusted bytes: [3](#0-2) 

Schnorr verification accepts the signature when `R + cA - sG` is identity: [4](#0-3) 

An attacker can construct a public key `A = xG + T`, where `T` is a non-identity torsion point and `x` is known. Although this key is not a legitimate prime-subgroup public key, deserialization accepts it. The attacker can then construct a Schnorr proof whose remaining torsion component cancels.

### Impact Explanation
This permits a forged Schnorr proof/signature for a malformed Ed448 public key that has no corresponding scalar private key under the intended prime-order group.

A concrete target is the PedPoP encryption-message proof of possession. The signature proves control of `msg.key`, with the challenge binding the nonce, key, sender, context, and encrypted payload: [5](#0-4) 

Accepting torsion keys allows an attacker to satisfy that proof for a malformed `msg.key`. The resulting ECDH shared point can also contain a torsion component, creating multiple possible shared keys and violating the protocol's assumption that all public points are in the prime-order subgroup.

### Likelihood Explanation
The malformed public key is directly supplied through the serialized `key` field and requires no private-key compromise or collusion. The proof-generation search only needs to match the residue of the Fiat-Shamir challenge modulo the small torsion order. For Ed448's cofactor of four, testing candidate torsion offsets gives a practical probability of finding a suitable challenge.

The issue is limited to uses of the custom `ed448` implementation and to callers that accept externally supplied Ed448 points or signatures. Ristretto and the `Ed25519` wrapper have explicit subgroup handling and are not affected by this implementation error.

### Recommendation
Implement a real prime-subgroup membership check in `Point::from_bytes`.

The check must verify that the decoded point has order `l`. The existing scalar type cannot express `l` because it is reduced modulo `l`; therefore use an unreduced scalar multiplication/check, a dedicated Ed448 subgroup-check routine, or a decoding representation that only admits prime-subgroup points.

Do not replace the current test with multiplication by the cofactor alone: `[4]P` maps arbitrary full-group points into the prime subgroup but does not establish that the original `P` was already in that subgroup.

### Proof of Concept
Let:

- `G` be the Ed448 prime-order generator.
- `T` be a non-identity point of order 4.
- `x` be an attacker-known scalar.
- `A = xG + T` be the submitted public key.
- `c = H(R, A, context/message)` be the Fiat-Shamir challenge.

Search over a nonce scalar `r` and torsion index `q ∈ {0,1,2,3}`:

```text
R_q = rG + qT
c_q = H(R_q, A, context/message)

Continue until:
q + c_q ≡ 0 mod 4
```

Then publish:

```text
R = rG + qT
s = r + c_q x
A = xG + T
```

Verification computes:

```text
R + cA - sG
= (rG + qT) + c(xG + T) - (r + cx)G
= (q + c)T
= identity
```

Thus `SchnorrSignature::verify` accepts the proof even though `A` is a malformed non-prime-order key accepted by `Point::from_bytes`.

### Citations

**File:** crypto/ed448/src/point.rs (L294-298)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-176)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-323)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
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
