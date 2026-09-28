### Title
Malformed per-participant preprocess vector crashes Bitcoin transaction signing - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignMachine::sign` assumes every participant supplies one `Preprocess` for every transaction input, but it indexes each participant's vector without checking its length. A participant can therefore provide an empty or truncated vector and trigger an out-of-bounds panic before validation or signing completes. [1](#0-0) 

### Finding Description
`TransactionSignMachine` contains one `AlgorithmSignMachine` per transaction input. [2](#0-1)  During `sign`, the implementation iterates over every input index `c` and indexes `commitments[c]` for each participant-supplied preprocess vector. [3](#0-2)  No code checks that every `Self::Preprocess` vector has `self.sigs.len()` elements before this indexing occurs. [1](#0-0)  An empty or short vector consequently causes Rust's indexing operation to panic while reorganizing the participant commitments. [3](#0-2) 

### Impact Explanation
A signing participant can abort transaction construction by crashing the local signing task or process. [3](#0-2)  The panic occurs after the local preprocess phase and before any invalid input is rejected with `FrostError`, so this is an input-dependent denial of service rather than an ordinary signature failure. [4](#0-3) [1](#0-0) 

### Likelihood Explanation
The attack requires the ability to participate in the signing set and provide a malformed `Vec<Preprocess<Secp256k1, ()>>` to `TransactionSignMachine::sign`. [5](#0-4)  No cryptographic construction, brute force, malformed curve point, or invalid signature is needed; the crash is caused solely by a missing vector-length invariant. [3](#0-2) 

### Recommendation
Before reorganizing the map, verify that every participant's preprocess vector contains exactly `self.sigs.len()` entries and return a `FrostError` on mismatch. [1](#0-0)  The same length validation should be applied to `TransactionSignatureMachine::complete`, which currently calls `shares.remove(0)` for each input without first checking that every participant supplied enough shares. [6](#0-5) 

### Proof of Concept
```rust
use std::collections::HashMap;
use frost::{Participant, sign::SignMachine};

// `machine` is produced by `TransactionMachine::preprocess`.
// A remote participant contributes a syntactically typed but truncated
// preprocess vector instead of one preprocess per transaction input.
let mut commitments = HashMap::new();
commitments.insert(Participant::new(2).unwrap(), Vec::new());

// Panics at `commitments[c]` when processing the first transaction input.
let _ = machine.sign(commitments, b"");
```

The panic path is the unchecked indexing performed while collecting `commitments[c]` for every signing machine. [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L302-318)
```rust
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
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L321-324)
```rust
pub struct TransactionSignMachine {
  tx: SignableTransaction,
  sigs: Vec<AlgorithmSignMachine<Secp256k1, Schnorr>>,
}
```

**File:** networks/bitcoin/src/wallet/send.rs (L326-370)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-420)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```
