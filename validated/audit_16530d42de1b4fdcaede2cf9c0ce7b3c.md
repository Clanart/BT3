### Title
Anyone can send dust outputs to a scanned multisig address, which are reported as received funds yet are economically unspendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` matches outputs solely by `script_pubkey` and records every match as a `ReceivedOutput`, with no minimum-value check. Mirroring the Fantium bug class ("anyone can change the balance by 1 wei"), any unprivileged party can craft a Bitcoin transaction paying a dust amount (e.g. 1 sat) to the multisig's P2TR address. The output is then reported to the processor as received funds, but spending it costs more in fee than it is worth, and it can never satisfy the wallet's own `DUST` payment threshold — the "received" funds are not spendable.

### Finding Description
`Scanner` indexes watched addresses in a `HashMap<ScriptBuf, Scalar>` and `scan_transaction` pushes a `ReceivedOutput` for every transaction output whose `script_pubkey` is present, unconditionally on `output.value` [1](#0-0) . `scan_block` forwards all matches, including coinbase outputs [2](#0-1) .

On the spend side, `SignableTransaction::new` rejects *payments* below `DUST = 546` but performs no equivalent check on *inputs* — any `ReceivedOutput`, including a 1-sat one, is accepted as an input and serialized into `prevouts`, committing to it via `Prevouts::All` in the sighash [3](#0-2) [4](#0-3) [5](#0-4) .

Each added input contributes a fixed ~230 WU (~58 vbytes: `OutPoint` + empty script_sig + sequence + 64-byte witness) to the transaction weight, as built in `calculate_weight_vbytes` [6](#0-5) . Any input whose value is below `fee_per_vbyte * ~58` is net-negative: including it strictly reduces the change/fee surplus the multisig retains. The `NotEnoughFunds` check only verifies the aggregate `input_sat >= payment_sat + needed_fee`, so dust inputs silently increase `needed_fee` while contributing essentially nothing [7](#0-6) .

### Impact Explanation
An attacker can continuously flood the multisig's scanned addresses (the base key script is public once known, and any previously registered offset script is equally targetable) with sub-fee-value outputs. Each such output is reported as received funds yet cannot be spent profitably, permanently encumbering the wallet's UTXO set and draining value through forced aggregation fees — the direct analog of "send 1 wei to block a state transition," except here every dust deposit imposes a recurring cost. Funds are reported received that are not economically spendable.

### Likelihood Explanation
Sending a dust output requires only a standard Bitcoin transaction to a publicly known address — no keys, no collusion, no validator access. The attacker can repeat it every block at the cost of the dust amount itself, which can be below the relay minimum per output via internal miner inclusion or batched outputs.

### Recommendation
Filter outputs in `Scanner::scan_transaction`/`scan_block` by a minimum economical value (e.g. reject outputs below `DUST`, or below the marginal input cost at a reference fee rate) before producing `ReceivedOutput`s; equivalently, drop sub-marginal inputs in `SignableTransaction::new` rather than signing for them.

### Proof of Concept
1. Observe the multisig group key; compute its watched script via `p2tr_script_buf(key)` (`Scanner::new`, `networks/bitcoin/src/wallet/mod.rs:162-166`).
2. Broadcast a transaction containing an output `{ script_pubkey: <watched script>, value: 1 sat }` (or any value below ~`58 * fee_per_vbyte`).
3. `scan_block` returns a `ReceivedOutput` for it; downstream scheduling must either spend it (net-negative input inflating `needed_fee` while `input_sat` gains 1 sat) or leave an ever-growing set of unspendable "received" outputs.

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L67-99)
```rust
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-221)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
