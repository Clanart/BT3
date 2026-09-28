### Title
Untrusted Bitcoin FROST preprocess commitments can force an infinity aggregate nonce and panic signing - ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs))

### Summary
The FROST preprocessing parser accepts canonical identity commitments without rejection. Those commitments are incorporated into the aggregate nonce `R`. Serai’s Bitcoin Schnorr `Hram` implementation unconditionally converts `R` and the group key to compressed SEC1 encodings and expects an x-coordinate, which panics for the point at infinity. Consequently, a participant-controlled preprocess containing identity/cancelling commitments can crash a signer during `sign` or `complete`.

### Finding Description
`Commitments::read` derives its expected shape from the algorithm and calls `GeneratorCommitments::read`, which calls `C::read_G` twice for each nonce commitment. There is no identity rejection in this path. [1](#0-0) [2](#0-1) 

The generic `Ciphersuite::read_G` accepts any canonical `GroupEncoding` value; it checks canonicality but does not reject `Group::identity()`. [3](#0-2) 

During `sign`, attacker-supplied commitments are inserted into the binding-factor map, then `B.nonces` computes each session nonce as `D + sum(rho * E)` over all participants. [4](#0-3) [5](#0-4) 

For Bitcoin, `Schnorr::verify` delegates to the IETF Schnorr implementation, whose `verify` constructs `SchnorrSignature { R: nonces[0][0], s: sum }` and verifies it. [6](#0-5) [7](#0-6) 

The Bitcoin `Hram::hram` calls `x(R)` and `x(A)`, and `x` panics on infinity because `encoded.x()` is `None` for the identity encoding. The implementation explicitly documents this panic condition. [8](#0-7) [9](#0-8) 

The same panic can happen earlier in `sign_share`, which calls `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` before producing a share. [10](#0-9) 

### Impact Explanation
An unauthenticated or low-privilege signing participant able to submit preprocess bytes can cause a process panic when the resulting aggregate nonce is the identity. Availability impact is medium: the signer does not return a recoverable `FrostError` or `io::Error`; it aborts via `expect("point at infinity")`.

This is analogous to the external report’s class: malformed public input reaches a parser/verification path that accepts a structurally valid encoding but assumes an incompatible semantic invariant, resulting in a crash.

### Likelihood Explanation
The bytes are reachable through `read_preprocess`, whose documented purpose is parsing peer preprocess messages. [11](#0-10) 

For a single ordinary Schnorr nonce, the attacker must make the binding-weighted commitment sum equal identity. Whether that is feasible depends on the surrounding protocol’s preprocess ordering and how many preprocesses the attacker controls. Identity commitments alone only zero the attacker’s contribution; reliable cancellation requires knowing or controlling enough other commitments. Therefore this is most plausible when the attacker controls all remote preprocesses for a signing attempt or can observe/calculate them before submission.

### Recommendation
Reject identity group elements in FROST commitment parsing or at the `Commitments`/`BindingFactor` boundary. For Bitcoin specifically, `Schnorr::verify`, `verify_share`, and `Hram::hram` should treat infinity as an invalid signature/share rather than panic. Additionally, reject any aggregate nonce `R` that is identity before invoking the BIP-340 challenge routine.

### Proof of Concept
Conceptually:

```rust
// Attacker-controlled preprocess bytes for one Schnorr nonce:
//   D = identity SEC1/k256 encoding
//   E = identity SEC1/k256 encoding
let mut preprocess = Vec::new();
preprocess.extend_from_slice(&ProjectivePoint::IDENTITY.to_bytes());
preprocess.extend_from_slice(&ProjectivePoint::IDENTITY.to_bytes());

machine.read_preprocess(&mut preprocess.as_slice())?;
```

When the aggregate `D + sum(rho_i * E_i)` across included participants is identity, `machine.sign(...)` reaches `Hram::hram(R = identity, A, msg)` and panics in `x(R)`. If signing completes, `machine.complete(...)` reaches the same panic through `Algorithm::verify`.

### Citations

**File:** crypto/frost/src/nonce.rs (L34-36)
```rust
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }
```

**File:** crypto/frost/src/nonce.rs (L133-139)
```rust
  pub(crate) fn read<R: Read>(reader: &mut R, generators: &[Vec<C::G>]) -> io::Result<Self> {
    let nonces = (0 .. generators.len())
      .map(|i| NonceCommitments::read(reader, &generators[i]))
      .collect::<Result<Vec<NonceCommitments<C>>, _>>()?;

    Ok(Commitments { nonces })
  }
```

**File:** crypto/frost/src/nonce.rs (L194-210)
```rust
  pub(crate) fn nonces(&self, planned_nonces: &[Vec<C::G>]) -> Vec<Vec<C::G>> {
    let mut nonces = Vec::with_capacity(planned_nonces.len());
    for n in 0 .. planned_nonces.len() {
      nonces.push(Vec::with_capacity(planned_nonces[n].len()));
      for g in 0 .. planned_nonces[n].len() {
        #[allow(non_snake_case)]
        let mut D = C::G::identity();
        let mut statements = Vec::with_capacity(self.0.len());
        #[allow(non_snake_case)]
        for IndividualBinding { commitments, binding_factors } in self.0.values() {
          D += commitments.nonces[n].generators[g].0[0];
          statements
            .push((binding_factors.as_ref().unwrap()[n], commitments.nonces[n].generators[g].0[1]));
        }
        nonces[n].push(D + multiexp_vartime(&statements));
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

**File:** crypto/frost/src/sign.rs (L276-280)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
```

**File:** crypto/frost/src/sign.rs (L348-383)
```rust
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }

    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);
```

**File:** networks/bitcoin/src/crypto.rs (L12-16)
```rust
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-67)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);
```

**File:** networks/bitcoin/src/crypto.rs (L139-149)
```rust
    fn verify(
      &self,
      group_key: ProjectivePoint,
      nonces: &[Vec<ProjectivePoint>],
      sum: Scalar,
    ) -> Option<Self::Signature> {
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
```

**File:** crypto/frost/src/algorithm.rs (L201-210)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** crypto/frost/src/algorithm.rs (L214-217)
```rust
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
  }
```
