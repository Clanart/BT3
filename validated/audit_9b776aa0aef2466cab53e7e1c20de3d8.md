### Title
Malformed Bitcoin preprocess vector panics transaction signing - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary

`TransactionSignMachine::sign` accepts each participant's Bitcoin signature preprocesses as a `HashMap<Participant, Vec<Preprocess<Secp256k1, ()>>>` and directly indexes that vector once per transaction input without checking that it contains one preprocess for every input. [1](#0-0) 

A public signing participant can supply an empty or truncated preprocess vector, causing `commitments[c]` to panic on the first missing index. [2](#0-1) 

### Finding Description

The transaction signing machine contains one inner Schnorr machine for each Bitcoin input. [3](#0-2)  During signing, it iterates over every input index and extracts `commitments[c]` from every participant's supplied preprocess vector. [2](#0-1) 

There is no validation that each supplied vector's length equals `self.sigs.len()`. [1](#0-0)  The deserializer does produce one nested preprocess for each input, but `sign` also accepts a caller-constructed map and does not preserve that invariant at the API boundary. [4](#0-3) 

As a result, a participant can supply a map entry whose value is `vec![]` or contains fewer than the number of transaction inputs. [2](#0-1)  Evaluation of `commitments[c]` then panics before the malformed input is converted into a `FrostError`. [5](#0-4) 

### Impact Explanation

This is an externally triggerable denial of service at the transaction-signing API boundary. [1](#0-0)  A signer can abort the calling thread or process with a panic instead of merely returning a malformed-participant error, potentially disrupting threshold-signing service availability. [6](#0-5) 

The panic occurs before the affected input's Schnorr machine produces a signature share, so one malformed preprocess vector can prevent the entire multi-input Bitcoin transaction from progressing. [7](#0-6) 

### Likelihood Explanation

A participant does not need cryptographic control over the transaction or a valid signature contribution; it only needs to pass a malformed vector into the public `sign` API. [1](#0-0)  The trigger is deterministic for any transaction with at least one input because index `0` is accessed unconditionally. [2](#0-1) 

If an integration exclusively uses `read_preprocess` and rejects trailing or missing bytes, the malformed vector cannot be produced by that parser because it reads one preprocess per configured input machine. [4](#0-3)  The vulnerability remains reachable whenever protocol inputs are routed or constructed through the public `HashMap` API without an equivalent length check. [8](#0-7) 

### Recommendation

Validate each supplied preprocess vector before indexing it. The map construction should either reject any vector whose length differs from `self.sigs.len()` or use `Vec::get(c)` and return a structured `FrostError` instead of panicking. Preferably encapsulate the preprocess collection so deserialization and direct construction both enforce the one-preprocess-per-input invariant.

### Proof of Concept

The following illustrates the panic path once a valid `TransactionSignMachine` has been constructed for a transaction with at least one input:

```rust
use std::collections::HashMap;

use frost::{Participant, curve::Secp256k1, sign::Preprocess};
use bitcoin_serai::wallet::TransactionSignMachine;

fn crash_signing(
  machine: TransactionSignMachine,
  malicious: Participant,
) {
  let mut preprocesses: HashMap<
    Participant,
    Vec<Preprocess<Secp256k1, ()>>,
  > = HashMap::new();

  // Missing the required per-input preprocess.
  preprocesses.insert(malicious, Vec::new());

  // Panics at `commitments[c]` when `c == 0`.
  let _ = machine.sign(preprocesses, b"");
}
```

The panic is caused by the unchecked indexing expression at `networks/bitcoin/src/wallet/send.rs:368`, which assumes every supplied vector contains one preprocess for each input. [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
```

**File:** networks/bitcoin/src/wallet/send.rs (L326-397)
```rust
impl SignMachine<Transaction> for TransactionSignMachine {
  type Params = ();
  type Keys = ThresholdKeys<Secp256k1>;
  type Preprocess = Vec<Preprocess<Secp256k1, ()>>;
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;
  type SignatureMachine = TransactionSignatureMachine;

  fn cache(self) -> CachedPreprocess {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }

  fn from_cache(
    (): (),
    _: ThresholdKeys<Secp256k1>,
    _: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    unimplemented!(
      "Bitcoin transactions don't support caching their preprocesses due to {}",
      "being already bound to a specific transaction"
    );
  }

  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }

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

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
