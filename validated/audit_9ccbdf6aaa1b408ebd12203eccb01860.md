### Title
Scanner reports immature coinbase outputs as spendable inputs, producing unbroadcastable transactions - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` iterates over **every** transaction in a block, including `txdata[0]` (the coinbase), and returns matching outputs as `ReceivedOutput`s indistinguishable from normally-spendable outputs. Coinbase outputs are encumbered by Bitcoin's 100-block maturity rule, so any `SignableTransaction` built from one is consensus-invalid and cannot be broadcast or mined.

### Finding Description
The MEV-boost incident's class — consuming/treating data as valid and actionable before its validity conditions are actually established — maps onto `Scanner::scan_block`:

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

`scan_transaction` (lines 199–214) matches solely on `output.script_pubkey`, with no check that the containing transaction is a coinbase. A `ReceivedOutput` produced from `block.txdata[0]` carries a valid `offset` and `outpoint`, is accepted by `SignableTransaction::new` (send.rs:150+), is signed by `TransactionSignMachine`/`TransactionSignatureMachine` via FROST, and yet the resulting transaction fails consensus validation (`bad-txns-premature-spend-of-coinbase`) until 100 confirmations elapse. The doc comment on `scan_block` itself acknowledges the maturity requirement exists ("bound by maturity"), but the API provides no maturity marking on `ReceivedOutput`, so nothing downstream can distinguish these outputs.

Reachability: any miner (fully unprivileged) can craft a coinbase paying the multisig's P2TR script (or any registered `key + offset·G` script, which is public). After `N::CONFIRMATIONS` (far fewer than 100), the scanner treats the output as finalized and reports it.

### Impact Explanation
Funds are reported as received and scheduled for spending while they are not yet spendable. The multisig will produce and attempt to broadcast a consensus-invalid transaction spending the immature coinbase output; the spend cannot succeed, the scheduler's plan is poisoned, and the threshold signing round consumes participant nonces/effort on a transaction that can never confirm in that form. Repeated attacks can be mounted at will by any miner at only the opportunity cost of directing a coinbase output.

### Likelihood Explanation
Requires a miner to direct a coinbase output to a Serai P2TR address — trivial for any miner, and can even happen accidentally (e.g., a pool paying out to a deposit address). No collusion or privileged access is needed; the input is a plain public Bitcoin transaction. Severity is bounded because the output does become spendable after maturity, so impact is delayed/DoS rather than permanent theft, and a corrected implementation can re-plan the spend once the output matures.

### Recommendation
In `scan_block`, skip `block.txdata[0]` (or skip `tx.is_coinbase()` entries) so coinbase outputs are never emitted as `ReceivedOutput`s, matching the fix implied by the existing doc comment. If coinbase deposits must be supported, annotate `ReceivedOutput` with a maturity height and have `SignableTransaction::new`/`Scheduler` reject or defer inputs whose coinbase maturity has not yet elapsed relative to the scanning block height.

### Proof of Concept
```rust
// Any miner constructs a coinbase paying the multisig script
let coinbase = Transaction {
  version: Version(2),
  lock_time: LockTime::ZERO,
  input: vec![/* coinbase input */],
  output: vec![TxOut {
    value: Amount::from_sat(50_0000_0000),
    script_pubkey: p2tr_script_buf(multisig_key).unwrap(),
  }],
};
let block: Block = /* block with txdata[0] = coinbase */;

let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput indistinguishable from a spendable one
assert_eq!(outputs.len(), 1);

// After N::CONFIRMATIONS (<< 100), it is scheduled:
let tx = SignableTransaction::new(outputs, &payments, change, None, fee).unwrap();
// FROST-sign it; the signed transaction is then broadcast and rejected
// with bad-txns-premature-spend-of-coinbase.
```

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L198-227)
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
  }

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

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
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
      .collect::<Vec<_>>();
```

**File:** processor/src/multisigs/scanner.rs (L559-567)
```rust
          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```
