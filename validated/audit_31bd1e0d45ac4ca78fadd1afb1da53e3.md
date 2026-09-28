### Title
Identity public keys enable universal Schnorr signature forgery - (File: crypto/schnorr/src/lib.rs)

### Summary
`SchnorrSignature::verify` accepts the identity group element as a public key, allowing anyone to forge a signature for an arbitrary message because the public-key term `cA` vanishes from the verification equation. [1](#0-0) 

### Finding Description
`Ciphersuite::read_G` accepts any canonically encoded group element, including the identity element. [2](#0-1)  When that identity encoding is used as a public key, `SchnorrSignature::verify` evaluates `R + cA - sG`; with `A = 0`, an attacker can choose any `s`, set `R = sG`, and produce a valid signature for any properly computed challenge `c`. [3](#0-2)  The FROST-specific `Curve::read_G` explicitly rejects identity points, showing that identity is not intended to be accepted in signing contexts. [4](#0-3) 

### Impact Explanation
An unprivileged party who can register or supply an identity public key can authenticate as that key and produce signatures for messages they were never authorized to sign. [5](#0-4) 

### Likelihood Explanation
The attack is deterministic and only requires canonical identity-point bytes plus public knowledge of the challenge derivation. [2](#0-1)  Exploitation requires a caller to accept an attacker-controlled Schnorr public key through `Ciphersuite::read_G` rather than an identity-rejecting authentication path. [4](#0-3) 

### Recommendation
Reject identity public keys in `SchnorrSignature::verify` or require all authentication public keys to be decoded through an identity-rejecting API equivalent to `Curve::read_G`. [4](#0-3)  Protocols should also reject identity keys during key registration rather than relying on signature verification alone. [2](#0-1) 

### Proof of Concept
```rust
// crypto/schnorr/src/lib.rs
let identity_bytes = <Secp256k1 as Ciphersuite>::G::identity().to_bytes();
let public_key = Secp256k1::read_G(&mut identity_bytes.as_ref()).unwrap();

let s = <Secp256k1 as Ciphersuite>::F::ONE;
let forged = SchnorrSignature::<Secp256k1> {
  R: Secp256k1::generator() * s,
  s,
};

// The verifier derives this publicly from R, public_key, and the message.
let challenge = protocol_challenge(&forged.R, &public_key, message);
assert!(forged.verify(public_key, challenge));
```
The forged statement is valid because `challenge * public_key` is the identity when `public_key` is the identity, leaving `R - sG = 0`. [6](#0-5)

### Citations

**File:** crypto/schnorr/src/lib.rs (L88-109)
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

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
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

**File:** crypto/frost/src/curve/mod.rs (L123-130)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
```
