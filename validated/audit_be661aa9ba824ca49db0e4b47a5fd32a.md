### Title
Scanner accepts dust-value outputs as spendable ReceivedOutputs, inflating reported funds - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_transaction` registers any transaction output whose `script_pubkey` matches a registered script, with no lower-bound check on `output.value`. This mirrors the Yearn yDAI slippage bug class (absent minimum-output/value protection): an unprivileged sender can create outputs worth less than the cost of spending them, and they are still reported as received, spendable funds.

### Finding Description
The scan loop matches only on `script_pubkey` and pushes a `ReceivedOutput` for any value, including 0:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
``` [1](#0-0) 

`ReceivedOutput::value()` exposes the amount as-is. [2](#0-1)  The codebase itself defines a dust bound (`DUST = 546`) but only enforces it on *payments* in `SignableTransaction::new`; inputs are accepted unconditionally:

```rust
for (_, amount) in payments {
  if *amount < DUST {
    Err(TransactionError::DustPayment)?;
  }
}
``` [3](#0-2) [4](#0-3) 

There is no `if input.output.value < DUST { skip }` anywhere in the scan or input-selection path. Any `ReceivedOutput`, however small, becomes a candidate `TxIn` once handed to `SignableTransaction::new`.

### Impact Explanation
- Funds reported received that are not spendable: a Taproot input costs ~230 weight units (~57 vbytes) to spend [5](#0-4) . At any nonzero fee rate, an output worth less than ~57 sats (and trivially anything under `DUST` = 546) cannot be spent profitably; below ~1 sat/vbyte it cannot even cover its own marginal fee.
- Including dust inputs inflates `needed_fee` (`fee_per_vbyte * vbytes` grows with input count) and `weight` without adding meaningful `input_sat`, which can push an otherwise-valid transaction into `NotEnoughFunds` or `TooLargeTransaction`, or cause the change calculation at line 228 to drop change that would have existed without the dust input. [6](#0-5) 
- An attacker can grief the wallet/coordinator by spamming cheap dust outputs to a known Serai P2TR script, forcing every dust output to be tracked and considered for inclusion, and permanently polluting the reported balance with unspendable sats.

### Likelihood Explanation
Reachable by any unprivileged party: `scan_transaction`/`scan_block` are fed raw on-chain transactions; anyone can craft a transaction paying a dust output to a registered script. The only mitigation is that a caller could filter results post-scan, but nothing in the in-scope code does this — the scanner's own docs only flag coinbase maturity, not dust. [7](#0-6) 

### Recommendation
Enforce the existing `DUST` constant on scanned outputs and on inputs to `SignableTransaction::new`:

```rust
// in scan_transaction
if output.value.to_sat() >= crate::wallet::DUST { ... }
```

or filter in `SignableTransaction::new` by skipping/`NotEnoughFunds`-accounting inputs below the spendable threshold, so reported balances reflect only economically spendable value.

### Proof of Concept
1. Observe a Serai P2TR script `S` (e.g., from `p2tr_script_buf(group_key)`).
2. Broadcast a transaction with a `TxOut { value: 100, script_pubkey: S }` (non-dust-relay but valid in a mined block, or 546 sats for relay-acceptable dust).
3. `Scanner::scan_transaction(&tx)` returns a `ReceivedOutput` with `value() == 100`, counted as received funds.
4. `SignableTransaction::new(vec![that_output], payments, ...)` adds it as an input; the marginal fee to spend it (~57 vbytes × fee rate) exceeds 100 sats, so the "received" funds can never be net-spent — the balance is permanently unspendable while still reported by `ReceivedOutput::value()`.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L71-82)
```rust
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-235)
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
```
