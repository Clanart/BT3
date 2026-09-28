### Title
Coinbase (immature) outputs reported as spendable `ReceivedOutput`s, producing transactions the network will reject - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]` (the coinbase transaction) and emits a `ReceivedOutput` for every coinbase output whose `script_pubkey` matches a registered key [1](#0-0) . `ReceivedOutput` carries no maturity/confirmation metadata — only `offset`, `output`, and `outpoint` [2](#0-1) . Downstream, `SignableTransaction::new` treats every `ReceivedOutput` as a freely spendable input and builds `TxIn`s for it without any coinbase-maturity check [3](#0-2) , and `TransactionMachine`/`TransactionSignMachine` will produce FROST signatures over it [4](#0-3) .

### Finding Description
Bitcoin consensus requires coinbase outputs to mature for 100 blocks before they can be spent. `scan_block` explicitly notes this ("This will also scan the coinbase transaction which is bound by maturity") but leaves enforcement to callers, and `scan_transaction` — callable directly on `block.txdata[0]` or any attacker-influenced context — performs no filtering at all [5](#0-4) . The result is an output reported "received" that is not spendable. Once inserted into `SignableTransaction::new`, it contributes its `value` to `input_sat`, passes `NotEnoughFunds` accounting, and yields a signed transaction whose coinbase input is consensus-invalid until maturity [6](#0-5) . This mirrors the incident class: accounting treats a balance as withdrawable when protocol rules forbid the withdrawal — the ATM dispensed cash against a deposit the network would not honor.

Additionally, `register_offset` makes offsets surjective: an offset that produces an odd key is silently incremented to `offset + 1` (or further) [7](#0-6) . Two distinct requested offsets can collapse onto the same registered scalar, and since `scripts` is keyed by script, registration order determines which logical "account" owns an incoming payment. An integrator deriving per-invoice offsets where `o₂ = o₁ + 1` and `key + G·o₁` is odd will have both invoices map to the same script, conflating funds.

### Impact Explanation
Funds reported received that are not spendable: a scanner feeding `scan_block`/`scan_transaction` results directly into transaction construction (the documented "immediately spendable" use) will sign and attempt to broadcast a transaction spending an immature coinbase input, which all nodes reject (non-standard/consensus-invalid until 100 confirmations). Worse, the `SignableTransaction` consumes that input's value in its accounting, so the failed transaction also locks in fee/change assumptions; an automated wallet can repeatedly retry and burn signing rounds (FROST preprocesses/signatures) on a transaction that can never confirm. The offset-collision additionally misattributes deposits between logical accounts sharing a script. Severity: Medium — requires the caller to act on immature coinbase outputs, but the API provides no guard and no maturity flag on `ReceivedOutput`.

### Likelihood Explanation
Any wallet/processor that scans blocks (rather than mempool/UTXO set) and pays to its own P2TR key can receive coinbase outputs — e.g., mining-pool payouts to the group key, or miners paying the Serai key directly. The only mitigation is an out-of-band post-processing pass mentioned in a doc comment; nothing in `ReceivedOutput`, `SignableTransaction::new`, or `multisig` (which only checks `script_pubkey` matches the offset group key [8](#0-7) ) rejects the immature input, so the failure surfaces only at broadcast time.

### Recommendation
- Enforce maturity in the scanner: in `scan_block`/`scan_transaction`, skip outputs of `tx.is_coinbase()` (or annotate `ReceivedOutput` with a maturity height and reject such inputs in `SignableTransaction::new`).
- Alternatively scan `block.txdata[1..]` internally and expose coinbase scanning behind a separate, clearly-marked API returning a distinct "immature output" type.
- For `register_offset`, document/enforce that callers must reject a returned offset differing from the requested one if offsets are derived from external indexes.

### Proof of Concept
Conceptual (regtest):

```rust
// key: even-Y ProjectivePoint owned by the wallet
let mut scanner = Scanner::new(key).unwrap();

// Mine a block whose coinbase pays p2tr_script_buf(key) — e.g. generatetoaddress.
let block = rpc.get_block(&rpc.get_block_hash(1).await.unwrap()).await.unwrap();

// scan_block / scan_transaction return the coinbase output as an ordinary ReceivedOutput
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1); // reported "received"

// The wallet builds a spend from it
let payment = (p2tr_script_buf(key).unwrap(), 1000);
let tx = SignableTransaction::new(vec![outputs.swap_remove(0)], &[payment], None, None, FEE)
  .unwrap(); // no maturity check — input_sat counts the coinbase value

// FROST signing succeeds; the signed TX is produced
let signed = sign(&keys, &tx);

// Broadcast fails: coinbase input is immature (BIP-113 / consensus rule)
rpc.send_raw_transaction(&signed).await.unwrap_err(); // "bad-txns-premature-spend-of-coinbase"
```

The codebase's own test acknowledges this gap by mining 100 blocks to maturity before scanning (`test_scanner`, `send_and_get_output` mines 100 maturity blocks after the paying block) [9](#0-8)  — confirming that without such external discipline, `scan_block` emits outputs that cannot actually be spent.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
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
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-227)
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
    res
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L215-255)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
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
