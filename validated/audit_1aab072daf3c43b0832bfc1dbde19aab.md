### Title
Coinbase outputs are reported as spendable before maturity - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in `block.txdata`, including the coinbase transaction, and converts matching outputs into `ReceivedOutput` values even though `ReceivedOutput` is defined as “a spendable output” and coinbase outputs cannot be spent until maturity. [1](#0-0) [2](#0-1) 

`SignableTransaction::new` then accepts these outputs solely based on their declared amount and creates a transaction spending their outpoints. [3](#0-2) [4](#0-3) 

This is analogous to treating an externally reported maximum/available balance as the actual spendable balance: the API reports an output as spendable before the consensus rules make it spendable.

### Finding Description
`ReceivedOutput` is explicitly documented as “A spendable output.” [5](#0-4) 

However, `Scanner::scan_block` iterates over every transaction in a block, including `block.txdata[0]`, and passes each transaction to `scan_transaction`. [6](#0-5) 

`scan_transaction` only compares each output’s `script_pubkey` to registered scripts; it has no coinbase or maturity check before constructing `ReceivedOutput`. [7](#0-6) 

The function’s comment acknowledges that coinbase outputs are returned and that callers must post-process them if they need immediately spendable outputs. [8](#0-7) 

That contract conflicts with the `ReceivedOutput` type invariant because the returned value is represented as spendable even when consensus rejects spending it. [1](#0-0) 

### Impact Explanation
A wallet or coordinator can receive a `ReceivedOutput`, schedule it as available input, construct a `SignableTransaction`, and initiate threshold signing for a transaction Bitcoin consensus will reject. [9](#0-8) 

`SignableTransaction::new` validates amounts, fees, outputs, dust, and transaction weight, but never checks whether an input’s transaction was coinbase or whether it has matured. [10](#0-9) [11](#0-10) 

`multisig` only verifies that each previous output’s script corresponds to the threshold key after applying the stored offset; it does not verify maturity. [12](#0-11) 

The resulting transaction can therefore be signed while being invalid under Bitcoin’s coinbase-maturity rule, causing funds to be reported as received/spendable when they are not yet spendable and potentially consuming signing rounds or blocking later scheduling. [13](#0-12) 

### Likelihood Explanation
Any miner can place a payout to a scanned Serai Taproot script in the coinbase transaction of a mined block. Once that block is supplied to `Scanner::scan_block`, the matching output is returned without maturity filtering. [14](#0-13) [6](#0-5) 

The project’s own tests demonstrate awareness of this requirement by mining 100 additional blocks before using a mined output as spendable. [15](#0-14) 

The issue is reachable from public Bitcoin block data and produces an incorrect spendability result, but it does not by itself move funds or leak key material; Medium severity is appropriate where downstream systems account for scanned outputs as immediately available.

### Recommendation
Do not return immature coinbase outputs through an API whose result type denotes spendable outputs. In `Scanner::scan_block`, skip `block.txdata[0]` when `tx.is_coinbase()` or require enough context to distinguish maturity, such as accepting the scanned block height and comparing it against the output’s coinbase status.

If immature outputs must be tracked for observability, introduce a distinct result type or metadata flag instead of returning them as `ReceivedOutput`, and make `SignableTransaction::new` reject inputs that cannot be spent yet.

### Proof of Concept
```rust
use bitcoin::{Block, ScriptBuf};
use k256::ProjectivePoint;

fn poc(block: &Block, key: ProjectivePoint, payment: ScriptBuf, change: ScriptBuf) {
  let scanner = Scanner::new(key).unwrap();

  // This includes block.txdata[0], the coinbase transaction.
  let outputs = scanner.scan_block(block);

  assert!(block.txdata[0].is_coinbase());

  // Find the coinbase output paid to the scanner's registered script.
  let coinbase_outpoint_txid = block.txdata[0].compute_txid();
  let coinbase_output = outputs
    .iter()
    .find(|output| output.outpoint().txid == coinbase_outpoint_txid)
    .cloned()
    .unwrap();

  // Accepted: the constructor validates value/fee accounting but not maturity.
  let spend = SignableTransaction::new(
    vec![coinbase_output],
    &[(payment, DUST)],
    Some(change),
    None,
    20,
  )
  .unwrap();

  // The returned transaction can proceed into `multisig`/threshold signing even
  // though Bitcoin rejects spending this input until coinbase maturity.
  let _signable = spend;
}
```

`scan_block` returns the coinbase output because matching is based only on `script_pubkey`, and `SignableTransaction::new` accepts it because construction checks only the supplied `ReceivedOutput` accounting and transaction limits. [7](#0-6) [3](#0-2) [16](#0-15)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-210)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-225)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L157-184)
```rust
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-243)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

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
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L270-284)
```rust
  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-389)
```rust
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
```

**File:** networks/bitcoin/tests/wallet.rs (L54-70)
```rust
  // Mine until maturity
  rpc
    .rpc_call::<Vec<String>>(
      "generatetoaddress",
      serde_json::json!([100, Address::p2sh(Script::new(), Network::Regtest).unwrap()]),
    )
    .await
    .unwrap();

  let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();

  let mut outputs = scanner.scan_block(&block);
  assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]));

  assert_eq!(outputs.len(), 1);
  assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
  assert_eq!(outputs[0].value(), block.txdata[0].output[0].value.to_sat());
```
