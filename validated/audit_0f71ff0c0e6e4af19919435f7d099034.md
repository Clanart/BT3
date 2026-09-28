### Title
Unauthenticated `ReceivedOutput` deserialization can create spendable-looking outputs that were never received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` trusts serialized `offset`, `TxOut`, and `OutPoint` fields without authenticating that the output was actually observed by `Scanner` on-chain. [1](#0-0) 

### Finding Description
Normally, `Scanner::scan_transaction` only creates a `ReceivedOutput` after matching `output.script_pubkey` against a registered key-derived script and binding it to the containing transaction’s real `OutPoint`. [2](#0-1)  In contrast, `ReceivedOutput::read` decodes all three fields independently from untrusted bytes and returns them as a struct documented as “a spendable output.” [3](#0-2)  `SignableTransaction::new` then trusts the supplied output’s value and outpoint as transaction inputs. [4](#0-3)  The later `multisig` check only verifies that the claimed previous output script corresponds to the threshold key plus the claimed offset; it does not prove that `outpoint` exists, contains that `TxOut`, or is unspent. [5](#0-4) 

### Impact Explanation
An attacker who can supply bytes later passed to `ReceivedOutput::read` can cause wallet state to report arbitrary Bitcoin value as spendable while the claimed `outpoint` is nonexistent, already spent, or references a different output. [6](#0-5)  This can inflate available balance and cause threshold signing of transactions that Bitcoin consensus will reject because their inputs do not resolve to the claimed UTXOs. [7](#0-6) 

### Likelihood Explanation
The attacker does not need a key share or validator role; they only need to influence serialized wallet bytes consumed through the explicitly exposed `ReceivedOutput::read` API. [8](#0-7)  The forged entry must use a script matching the wallet key and offset to pass `SignableTransaction::multisig`, which is feasible when the target’s wallet script is public. [5](#0-4) 

### Recommendation
Do not allow raw deserialization to mint a semantically “received” output. Require reconstruction through `Scanner::scan_transaction`, or authenticate persisted outputs and revalidate the outpoint against Bitcoin before using it as an input. At minimum, `SignableTransaction::new`/`multisig` should require proof that `outpoint` resolves to the exact serialized `TxOut` and is unspent. [9](#0-8) 

### Proof of Concept
1. Obtain the wallet’s Taproot `script_pubkey` and a registered `offset`.
2. Serialize attacker-chosen bytes containing that offset, a high-value `TxOut` with the wallet script, and an arbitrary/nonexistent `OutPoint`.
3. Pass the bytes to `ReceivedOutput::read`; it succeeds because it performs no provenance check. [6](#0-5) 
4. Pass the resulting object to `SignableTransaction::new`; its value is counted as input balance. [10](#0-9) 
5. `multisig` accepts the input if the fake `TxOut` script matches `keys.offset(offset).group_key()`, even though the `outpoint` is fake. [5](#0-4)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
```rust
  /// Scan a transaction.
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
      }
    }
    res
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-184)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```
