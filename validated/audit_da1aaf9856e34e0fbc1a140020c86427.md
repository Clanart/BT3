### Title
`Scanner::scan_transaction` accepts zero-value/dust outputs, forcing uneconomical aggregation of attacker-controlled UTXOs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` registers any transaction output whose `script_pubkey` matches a registered key/offset as a `ReceivedOutput`, with no check on `output.value`. This mirrors the "no `minLoanSize`" bug class: just as a zero minimum loan size leaves no incentive to liquidate small underwater positions, a zero minimum scanned output value lets an unprivileged attacker flood the vault with dust outputs that cost more to spend/aggregate than they are worth, saddling the protocol with uneconomical cleanup.

### Finding Description
`ReceivedOutput` is constructed for every matching output with no lower bound on its value [1](#0-0) . The only filter is `self.scripts.get(&output.script_pubkey)`, so a 0-sat or 1-sat P2TR output to a scanned key is reported identically to a real deposit. The `value()` accessor exposes the raw amount with no validation [2](#0-1) , and `ReceivedOutput::read` similarly deserializes any `TxOut` without a value check [3](#0-2) .

The crate clearly recognizes the concept of a minimum economical value: `SignableTransaction::new` rejects payments below `DUST` [4](#0-3)  and refuses to create change below `DUST` [5](#0-4) . That bound exists only on the send path; nothing enforces it on the receive/scan path, where the bytes are attacker-controlled.

### Impact Explanation
Any Bitcoin user can send arbitrarily many dust or zero-value outputs to a Serai vault address (the script_pubkey is public/derivable). Each is reported as a spendable `ReceivedOutput`. Spending such an input costs more in fees than its value, so the protocol either bleeds funds aggregating worthless inputs or must special-case them downstream. This is the same "cheap attack creating many small positions nobody is incentivized to clean up" pattern as the source report: the cost of the cleanup transaction exceeds the value recovered, and the loss falls on the protocol/users. In the worst case `value < per-input spend cost` means the wallet effectively reports "funds received" that are net-negative when spent.

### Likelihood Explanation
The attack requires only standard Bitcoin transactions to a known address — no validator status, no collusion, no leaked keys. Cost to the attacker is bounded by dust-level output values (or literally zero-value outputs, which are consensus-valid) plus minimal fees, while each created output imposes a nonzero future spend cost on the protocol. The missing check is unconditional in the scanning code, so exploitation is deterministic once deposits arrive.

### Recommendation
Enforce a minimum value when scanning: in `Scanner::scan_transaction` (and/or `ReceivedOutput::read`), skip or reject outputs whose `output.value` is below the dust/economic-spendability threshold, consistent with the `DUST` bound already applied in `send.rs`. Alternatively, document and enforce at the type level that `ReceivedOutput` guarantees `value >= DUST` so downstream aggregation cannot be fed uneconomical inputs.

### Proof of Concept
1. Compute a Serai vault's P2TR script via `p2tr_script_buf` / `register_offset` (all public data).
2. Broadcast a Bitcoin transaction containing N outputs to that script with `value = 1` sat (or `0`) each.
3. `Scanner::scan_block`/`scan_transaction` returns N `ReceivedOutput`s indistinguishable in kind from legitimate deposits [1](#0-0) .
4. Any spend of these inputs via `SignableTransaction::new` consumes ~57+ vbytes per input [6](#0-5) , so the fee to spend each input exceeds its value — a guaranteed-loss cleanup identical in shape to unliquidatable dust loans.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L115-118)
```rust
  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
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
