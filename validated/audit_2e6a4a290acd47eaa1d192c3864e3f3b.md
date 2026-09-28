### Title
Unauthenticated `ReceivedOutput` inputs can cause unintended vault UTXOs to be signed - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`SignableTransaction::new` accepts every supplied `ReceivedOutput` as an input without proving that the output was intentionally allocated to the transaction. `ReceivedOutput::read` accepts the offset, `TxOut`, and `OutPoint` directly from serialized bytes, while `SignableTransaction::multisig` checks only that each claimed prevout is controlled by the threshold key, not that the referenced UTXO was authorized for this spend. [1](#0-0) [2](#0-1) 

### Finding Description

An attacker who can provide serialized `ReceivedOutput` bytes or otherwise influence the input list can append an outpoint belonging to the same threshold wallet. `ReceivedOutput::read` does not authenticate the supplied fields or bind them to a transaction plan. [1](#0-0) 

`SignableTransaction::new` converts all supplied inputs into `TxIn`s and stores all supplied `TxOut`s as `Prevouts::All` data. [3](#0-2) [4](#0-3) 

Before creating signing machines, `multisig` verifies only that `key + offset*G` produces the supplied prevout's `script_pubkey`; it does not check whether that prevout was selected or authorized for the requested payment. [5](#0-4) 

During signing, every added input receives a Taproot key-spend signature over `Prevouts::All`, so a correctly described vault UTXO is signed and spent together with the intended inputs. [6](#0-5) 

### Impact Explanation

If an input list is assembled from attacker-controlled messages, an unprivileged party can cause the threshold wallet to sign a transaction spending an unrelated wallet-owned UTXO. The attacker can direct the transaction's payment or change output to an address they control, producing an unintended valid spend rather than merely a malformed transaction. [7](#0-6) [8](#0-7) 

For a UTXO sent to the wallet's base Taproot script, the attacker only needs the publicly visible `OutPoint` and `TxOut` and can use a zero offset. [9](#0-8) [10](#0-9) 

### Likelihood Explanation

Bitcoin outpoints and their `TxOut`s are public chain data, so obtaining the metadata required for a wallet-owned input does not require private information. [9](#0-8) 

The vulnerability requires a deployment path to accept serialized or externally influenced `ReceivedOutput` values when constructing a `SignableTransaction`; the exposed `read` and transaction-construction APIs provide the relevant boundary. [1](#0-0) [11](#0-10) 

### Recommendation

Bind each `ReceivedOutput` to an authenticated spend plan before calling `SignableTransaction::new`, and reject deserialized `ReceivedOutput`s that were not explicitly authorized for that plan. At minimum, include an allowlist of authorized outpoints in the signing context and compare every `input.outpoint` against it before producing signature shares. [11](#0-10) [2](#0-1) 

The check should happen before `TransactionSignMachine::sign`, because that function signs every transaction input after the `SignableTransaction` has been accepted. [12](#0-11) 

### Proof of Concept

```rust
// Conceptual PoC against networks/bitcoin/src/wallet/send.rs.

// The attacker observes a wallet-owned base-address UTXO on chain:
//   target_outpoint: OutPoint
//   target_txout:    TxOut

// Serialize an attacker-controlled ReceivedOutput.
// For the wallet's base Taproot script, the offset is Scalar::ZERO.
let mut encoded = Vec::new();
encoded.extend_from_slice(&Scalar::ZERO.to_bytes());
target_txout.consensus_encode(&mut encoded).unwrap();
target_outpoint.consensus_encode(&mut encoded).unwrap();

// This succeeds because read only decodes fields; it does not authenticate
// the outpoint or bind it to the transaction's spend authorization.
let injected = ReceivedOutput::read(&mut encoded.as_slice()).unwrap();

let mut inputs = intended_inputs;
inputs.push(injected);

let signable = SignableTransaction::new(
  inputs,
  &attacker_payments,
  attacker_change,
  None,
  fee_per_vbyte,
).unwrap();

let machine = signable.multisig(&threshold_keys).unwrap();
```

`multisig` accepts the injected input when its public `TxOut` has the wallet's base Taproot `script_pubkey`, because the zero offset produces that script. [5](#0-4)  The subsequent signing loop then creates a threshold signature share for the injected input, committing to all supplied prevouts and authorizing the unintended spend. [6](#0-5)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-234)
```rust
    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L355-397)
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

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
