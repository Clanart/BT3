### Title
Malicious FROST participant can crash transaction signing with an identity nonce commitment - ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs))

### Summary
An unprivileged signing participant can submit a syntactically valid FROST preprocess whose nonce commitments produce an aggregate Taproot nonce `R` at the point at infinity. `TransactionSignMachine::sign` passes that aggregate nonce into the Bitcoin BIP-340 challenge implementation, which unconditionally extracts the nonce's x-coordinate and panics on infinity. The malformed preprocess is rejected neither by `read_preprocess` nor by the general FROST signing-set validation before secret-dependent share generation begins.

### Finding Description
FROST preprocesses supplied by other participants are read by `AlgorithmSignMachine::read_preprocess`, which delegates nonce commitments to `Commitments::read` and then reads the algorithm addendum [1](#0-0) . The signing-set checks validate participant count, bounds, and duplicates, but do not validate that any submitted nonce commitment, or the bound aggregate nonce, is non-identity [2](#0-1) .

For Bitcoin transactions, each participant's preprocess is a vector containing one FROST preprocess per transaction input [3](#0-2) . `TransactionSignMachine::sign` indexes those preprocesses and invokes each per-input signer with the Taproot key-spend sighash [4](#0-3) .

The Bitcoin Schnorr algorithm's challenge function calls `x(R)`, and `x` panics with `"point at infinity"` if `R` is identity [5](#0-4) . `Hram::hram` passes the aggregate nonce directly to `x(R)` before reducing the resulting hash [6](#0-5) . Therefore, a preprocess that produces an identity aggregate nonce does not produce a `FrostError::InvalidPreprocess`; it aborts the signer.

This is analogous to the reported external-resource mutation bug: a participant can first participate normally enough to be included in the signing set, then provide state/data that causes the recovery/signing path to fail through an unhandled exceptional condition rather than attributable protocol rejection.

### Impact Explanation
A single malicious participant can prevent completion of a Bitcoin transaction-signing attempt by causing honest processors to panic while deriving their shares. Because this occurs after preprocess exchange and before usable signature shares are emitted, the transaction cannot complete in that attempt. If the same participant is repeatedly selected or the malformed message is republished, operators may be forced into manual recovery or validator exclusion before funds can be spent.

The issue affects funds already controlled by the threshold Bitcoin key: they remain locked until a signing round excluding the faulty participant succeeds. A crash of an honest processor can also interrupt unrelated signing work handled by that process.

### Likelihood Explanation
The attacker only needs to be a participant able to submit a preprocess message. They control the nonce-commitment bytes consumed by `read_preprocess`, and the current parser imposes no semantic nonce-validity check [1](#0-0) . Transaction signing explicitly accepts those participant preprocesses and feeds them into per-input signing [7](#0-6) .

The practical feasibility depends on whether the attacker can make the bound aggregate nonce identity under the participant-specific binding factors. They can try candidate nonce commitments offline because the binding-factor transcript is derived from public preprocesses, the group key, and the transaction sighash/message [8](#0-7) . At minimum, directly submitted identity commitments should be rejected rather than allowed to reach code documented to panic on identity `R`.

### Recommendation
Validate nonce commitments and aggregate nonces before share generation:

- Reject identity nonce commitments in FROST `Commitments::read` or during preprocess validation.
- After binding factors are applied, reject any identity aggregate `R` with `FrostError::InvalidPreprocess(participant)` or another attributable error before `sign_share`.
- Ensure Bitcoin `Hram`/`Schnorr` is only reached with non-infinity nonce points, and avoid panics for adversary-controlled preprocess combinations.
- Add regression coverage where a malicious participant supplies identity/torsion-adjacent nonce commitments for a multi-input Bitcoin transaction.

### Proof of Concept
The vulnerable path is:

```rust
// crypto/frost/src/sign.rs
fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
  Ok(Preprocess {
    commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
    addendum: self.params.algorithm.read_addendum(reader)?,
  })
}
```

```rust
// networks/bitcoin/src/wallet/send.rs
let commitments = (0 .. self.sigs.len())
  .map(|c| {
    commitments
      .iter()
      .map(|(l, commitments)| (*l, commitments[c].clone()))
      .collect::<HashMap<_, _>>()
  })
  .collect::<Vec<_>>();
```

```rust
// networks/bitcoin/src/crypto.rs
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

A malicious preprocess byte stream is parsed by `read_preprocess`, inserted into the participant map, and used by `TransactionSignMachine::sign`. If its nonce commitments yield an aggregate nonce at infinity for any transaction input, `Hram::hram` calls `x(R)` and the process panics instead of returning an attributable `FrostError`. This mirrors the report's unhandled invalid-state path: the malicious party's object remains structurally parseable, but makes the protected completion path unusable.

### Citations

**File:** crypto/frost/src/sign.rs (L276-280)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
```

**File:** crypto/frost/src/sign.rs (L290-313)
```rust
    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```

**File:** crypto/frost/src/sign.rs (L361-379)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L297-317)
```rust
impl PreprocessMachine for TransactionMachine {
  type Preprocess = Vec<Preprocess<Secp256k1, ()>>;
  type Signature = Transaction;
  type SignMachine = TransactionSignMachine;

  fn preprocess<R: RngCore + CryptoRng>(
    mut self,
    rng: &mut R,
  ) -> (Self::SignMachine, Self::Preprocess) {
    let mut preprocesses = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .map(|sig| {
        let (sig, preprocess) = sig.preprocess(rng);
        preprocesses.push(preprocess);
        sig
      })
      .collect();

    (TransactionSignMachine { tx: self.tx, sigs }, preprocesses)
```

**File:** networks/bitcoin/src/wallet/send.rs (L355-395)
```rust
  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;
```

**File:** networks/bitcoin/src/crypto.rs (L10-16)
```rust
/// Get the x coordinate of a non-infinity point.
///
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-70)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
```
