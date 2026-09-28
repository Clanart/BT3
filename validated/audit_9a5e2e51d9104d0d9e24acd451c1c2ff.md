### Title
Ed448 identity-point encoding panics `Ciphersuite::read_G` via unconditional `invert().unwrap()`, enabling unauthenticated denial of service — (File: crypto/ed448/src/point.rs)

### Summary
An unauthenticated remote party can crash any Serai component that deserializes an Ed448 point from untrusted bytes. `Point::from_bytes` accepts the encoding of the identity point (y = 1, sign bit = 0), but the canonical-encoding round-trip check inside `Ciphersuite::read_G` then calls `Point::to_bytes`, which unconditionally unwraps the inversion of `z = 0` and panics. Every in-scope `read_G` consumer (`Commitments::read`, `SchnorrSignature::read`, `DLEqProof::read`, `EncryptedMessage::read`, `EncryptionKeyProof::read`, `ThresholdKeys::read`, `read_preprocess`, `read_share`) is reachable with attacker-controlled bytes, so a single 57-byte message aborts the process. This is the Serai analog of CVE-2022-21283's bug class: an unauthenticated network attacker causing a partial denial of service purely by supplying crafted input to a library parsing routine.

### Finding Description
`GroupEncoding::from_bytes` for `crypto/ed448/src/point.rs` extracts the sign bit, recovers `x` from `y`, and rejects only "negative zero" (x = 0 with sign = 1) and torsion points [1](#0-0) . The identity point (x = 0, y = 1, z = 0, sign = 0) passes both checks: `x.is_zero() & sign` is false, and the identity is torsion-free, so `from_bytes` returns `Some(identity)`.

`Ciphersuite::read_G` then enforces canonical encoding by re-serializing the point and comparing bytes [2](#0-1) . `Point::to_bytes` computes `let z = self.z.invert().unwrap();` [3](#0-2) . For the identity, `z = 0`, `invert()` returns `CtOption::none`, and `.unwrap()` panics.

This panic happens inside `Ciphersuite::read_G` itself, before the identity-rejection in `Curve::read_G` can run [4](#0-3) , so the guard that was intended to handle identity points is never reached. There is no `catch_unwind` or error path; deserialization of a validly-checksummed but semantically degenerate point is fatal.

### Impact Explanation
Any protocol step that reads an Ed448 point from a counterparty — FROST `read_preprocess`/`read_share` over `Ed448` [5](#0-4) , PedPoP `Commitments::read` and `EncryptedMessage::read` [6](#0-5) [7](#0-6) , or `EncryptionKeyProof::read` in blame handling [8](#0-7)  — can be crashed by a single malicious message. In a DKG or signing session this aborts the victim participant mid-protocol, and since preprocesses/`CachedPreprocess` are single-use, repeated crashes across session attempts give a persistent denial of service against the threshold group whenever the Ed448 ciphersuite is deployed. This matches the CVE class: unauthenticated, network-reachable, partial DOS via a library component parsing untrusted input.

### Likelihood Explanation
Triggering is trivial and deterministic: the attacker serializes the identity point — `0x01` followed by 56 zero bytes with the top bit of the last byte clear — and sends it wherever a point is expected (e.g., as `key` in `EncryptedMessage`, a commitment in `Commitments::read`, `R` in `SchnorrSignature::read`, or `key`/`dleq` components in `EncryptionKeyProof::read`). No secrets, valid proofs, or protocol state are required; the message just needs to parse far enough to reach `read_G`. The only requirement is that the deployment uses the `Ed448` ciphersuite, since the panic lives in Ed448's `to_bytes` (dalek-based curves serialize the identity without panicking). Medium likelihood under the assumption that an Ed448 deployment or a mixed-ciphersuite deployment accepts these messages.

### Recommendation
In `Point::to_bytes` (crypto/ed448/src/point.rs), do not unwrap `self.z.invert()` unconditionally. Handle the non-invertible `z` (identity) case explicitly — e.g., return the canonical identity encoding directly, or restructure so identity never reaches `to_bytes`. Alternatively, reject the identity inside `Point::from_bytes` (alongside the negative-zero/torsion checks) so `Ciphersuite::read_G` errors before the re-serialization step. A regression test feeding the 57-byte identity encoding through `<Ed448 as Ciphersuite>::read_G` and asserting a clean `Err` should be added.

### Proof of Concept
```
// Ed448 identity encoding: y = 1 little-endian (0x01 || 0x00 * 55 || 0x00), sign bit = 0
let identity_encoding: [u8; 57] = {
    let mut b = [0u8; 57];
    b[0] = 1;
    b
};

// This panics inside Point::to_bytes at crypto/ed448/src/point.rs:326
// because z == 0 makes invert() return CtOption::none which is unwrapped.
let _ = <Ed448 as Ciphersuite>::read_G(&mut identity_encoding.as_ref());
```

Reachable wrappers hit identically: `SchnorrSignature::<Ed448>::read`, `DLEqProof::<Ed448Point>::read`, `EncryptedMessage::<Ed448, _>::read`, `Commitments::<Ed448>::read`, `EncryptionKeyProof::<Ed448>::read`, and `AlgorithmSignMachine::read_preprocess` — each forwards to `C::read_G`, which calls `to_bytes` for the canonical-encoding comparison and panics on the identity before `Curve::read_G`'s `is_identity` rejection can execute [9](#0-8) [10](#0-9) .

### Citations

**File:** crypto/ed448/src/point.rs (L303-318)
```rust
  fn from_bytes(bytes: &Self::Repr) -> CtOption<Self> {
    // Extract and clear the sign bit
    let sign = Choice::from(bytes[56] >> 7);
    let mut bytes = *bytes;
    let mut_ref: &mut [u8] = bytes.as_mut();
    mut_ref[56] &= !(1 << 7);

    // Parse y, recover x
    FieldElement::from_repr(bytes).and_then(|y| {
      recover_x(y).and_then(|mut x| {
        x.conditional_negate(x.is_odd().ct_eq(&!sign));
        let not_negative_zero = !(x.is_zero() & sign);
        let point = Point { x, y, z: FieldElement::ONE };
        CtOption::new(point, not_negative_zero & point.is_torsion_free())
      })
    })
```

**File:** crypto/ed448/src/point.rs (L325-334)
```rust
  fn to_bytes(&self) -> Self::Repr {
    let z = self.z.invert().unwrap();
    let x = self.x * z;
    let y = self.y * z;

    let mut bytes = y.to_repr();
    let mut_ref: &mut [u8] = bytes.as_mut();
    mut_ref[56] |= x.is_odd().unwrap_u8() << 7;
    bytes
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

**File:** crypto/frost/src/sign.rs (L276-281)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L266-269)
```rust
impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }
```
