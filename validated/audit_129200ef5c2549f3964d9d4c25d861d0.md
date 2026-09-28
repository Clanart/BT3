### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable funds - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over every transaction in `block.txdata`, including the coinbase transaction at index 0. Any output in the coinbase paying to a watched P2TR script is returned as a `ReceivedOutput` indistinguishable from an ordinary, immediately-spendable output. Coinbase outputs, however, cannot be spent for 100 blocks (BIP-30 / consensus rule). The result is "funds reported received that are not spendable", mirroring the reference issue where the `withdraw` path changed token balances without the corresponding `Minted`/`Burned` accounting — here the scanner reports balance without the maturity flag that determines whether it can actually be spent.

### Finding Description
`scan_block` does not distinguish the coinbase transaction:

```rust
// networks/bitcoin/src/wallet/mod.rs:221-227
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` (mod.rs:199-214) matches only `output.script_pubkey` and builds a `ReceivedOutput { offset, output, outpoint }` with no field recording that the outpoint refers to a coinbase. The doc comment at mod.rs:217-220 acknowledges the hazard ("If received outputs must be immediately spendable, a post-processing pass is needed") but the type carries no maturity indicator and the API silently mixes immature and spendable outputs in one `Vec`.

Downstream, `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) accepts any `Vec<ReceivedOutput>` and unconditionally builds `TxIn`s spending their outpoints; `TransactionMachine` then produces a validly-signed transaction that consensus will reject (`bad-txns-premature-spend-of-coinbase`) as long as the coinbase is under 100 confirmations. Nothing in the wallet layer prevents or flags this.

### Impact Explanation
- Any miner is an unprivileged party who can place an output paying the multisig's scanned script in their coinbase transaction — this requires only publicly known data (the P2TR script).
- Consumers of `scan_block` that feed results into `SignableTransaction::new` (the documented intended flow) will construct and sign a transaction which is unbroadcastable for up to ~100 blocks. The honest signer set expends a full FROST signing round on a transaction that cannot confirm, and depending on the scheduler may burn the allocated nonce/preprocess ceremony and stall the payment batch.
- Accounting-wise, the scanner overstates immediately-available balance by the immature amount, exactly the class of "state change not tracked correctly" in the reference report.

### Likelihood Explanation
Requires a miner to send a coinbase output to a watched script, or an integrator to use `scan_block` naively (it is the natural API for block scanning). Triggering is cheap for any miner; the failure mode (signed-but-invalid transaction) only resolves after maturity, so the practical impact is a delayed/failed signing round and temporarily unusable reported funds rather than permanent loss — consistent with Medium severity.

### Recommendation
Either:
- Skip `block.txdata[0]` inside `scan_block`, or
- Add a maturity/coinbase flag to `ReceivedOutput` (e.g., `is_coinbase: bool` or a `maturity_height`) and have `SignableTransaction::new` reject immature coinbase inputs.

Do not rely on a comment directing every caller to re-implement the filter.

### Proof of Concept
```rust
// Miner crafts a block whose coinbase pays to the multisig script:
let block: Block = ...; // block.txdata[0] is the coinbase with
                        // txdata[0].output[0].script_pubkey == watched_script
let outputs = Scanner::new(group_key).unwrap().scan_block(&block);
// outputs[0] is a ReceivedOutput with offset ZERO and the coinbase outpoint.
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));

// It is accepted as a spendable input:
let stx = SignableTransaction::new(
  vec![outputs[0].clone()],
  &[(payment_script, amount)],
  Some(change_script),
  None,
  fee_per_vbyte,
).unwrap();
// After multisig() + FROST signing, the produced Transaction spends an
// immature coinbase output and is rejected by consensus for ~100 blocks.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
