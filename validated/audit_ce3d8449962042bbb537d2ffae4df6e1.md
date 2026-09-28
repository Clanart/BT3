### Title
`ThresholdKeys::read` accepts identity verification shares, allowing an attacker-defined (even identity) group key - (File: crypto/dkg/src/lib.rs)

### Summary
The SRP-6a `A=0` bug class — accepting a client-supplied identity/zero group element where a non-identity value is required — maps directly onto Serai's `ThresholdKeys` deserialization. `ThresholdKeys::read` reads all `n` verification shares with `<C as Ciphersuite>::read_G`, which enforces only canonical encoding, not non-identity. The FROST layer explicitly recognizes this distinction: `Curve::read_G` exists solely to additionally reject the identity point, yet `ThresholdKeys::read` (a `dkg` type, generic over `Ciphersuite`) cannot use it. An identity or attacker-chosen verification share flows unchecked into `ThresholdKeys::new`, which derives `group_key` by interpolating shares `1..=t` with no identity check on either the shares or the result.

### Finding Description
`Ciphersuite::read_G` checks only that the point decodes and re-encodes canonically [1](#0-0) . `Curve::read_G` was added because FROST recognized identity must be rejected [2](#0-1) . `ThresholdKeys::read` deserializes `secret_share` via `read_F` (zero allowed) and every `verification_shares` entry via `<C as Ciphersuite>::read_G` — identity allowed — then calls `ThresholdKeys::new` [3](#0-2) . `ThresholdKeys::new` validates share count and participant indexes, but never validates share values; `group_key` is computed as `sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` [4](#0-3) . The same identity-accepting `C::read_G` is used on peer-controlled bytes in PedPoP: `EncryptionKeyMessage::read` (the ECDH key), `EncryptedMessage::read` (`key`), `EncryptionKeyProof::read` (`key`), and `Commitments::read` (all FELDT commitments, including `commitments[0]` which serves as the PoK public key) [5](#0-4) [6](#0-5) .

### Impact Explanation
- **Attacker-controlled group key**: bytes fed to `ThresholdKeys::read` with identity verification shares for indexes `1..=t` yield `group_key = identity`. A Schnorr signature under an identity public key is forgeable by anyone (`R + c·A - s·G = 0` for any `R = s·G` since `c·A = 0`), and any wallet/scanner keyed on this group key reports funds "received" that are spendable by no one honestly — or effectively by anyone through forgery against naive verifiers.
- **Zero secret share**: `read_F` accepts `0`, so a deserialized key can carry `secret_share = 0` paired with `verification_share = identity` while passing `ThresholdKeys::new`.
- **Trivially forged PoK in PedPoP**: `verify_r1` batch-verifies `msg.sig` against `msg.commitments[0]` as the Schnorr public key [7](#0-6) . If `commitments[0]` is identity, any `(R, s)` with `R = s·G` verifies regardless of challenge — the proof-of-knowledge check on the constant coefficient is bypassed structurally rather than cryptographically.
- **Identity ECDH key**: `EncryptedMessage::read` accepts `key = identity`, making `ecdh = enc_key · identity = identity` — a publicly known shared key, so the "encrypted" share plaintext is fully attacker-determined [8](#0-7) .

### Likelihood Explanation
Anywhere serialized `ThresholdKeys` or PedPoP messages cross a trust boundary (coordinator-provided DKG payloads, stored key material influenced by an attacker), a single identity point in the byte stream is accepted. Exploitation requires no computation — the identity is a canonical encoding — analogous to sending `A=0` in the KeePassRPC bug.

### Recommendation
Reject identity wherever a group element represents a public key, commitment, or ECDH key: use a non-identity-checking-aware read (equivalent to `Curve::read_G`) inside `ThresholdKeys::read`, `Commitments::read`, `EncryptionKeyMessage::read`, `EncryptedMessage::read`, and `EncryptionKeyProof::read`, and assert `!group_key.is_identity()` in `ThresholdKeys::new`.

### Proof of Concept
Serialize a `ThresholdKeys` blob: valid `C::ID`, `t=2, n=3, i=1`, `Interpolation::Lagrange` (byte `1`), `secret_share = 0`, and three identity-point encodings for the verification shares. `ThresholdKeys::read` returns `Ok` and `group_key()` is the identity point. Any Schnorr signature `(R = s·G, s)` then verifies against that group key for arbitrary challenge, and `verify_share` for each participant degenerates since `verification_share = identity`.

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

**File:** crypto/dkg/src/lib.rs (L361-379)
```rust
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

```

**File:** crypto/dkg/src/lib.rs (L618-631)
```rust
    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L115-127)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L323-329)
```rust
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );
```
