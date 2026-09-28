### Title
`Scanner::scan_block` reports coinbase outputs as received despite their 100-block maturity, producing "received" funds that cannot actually be spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Solidity report class is a transfer whose failure is silently swallowed while the code proceeds as if it succeeded — here, assets are written off / accounted for even though no real spendable value moved. In `bitcoin-serai`, `Scanner::scan_block` iterates over `block.txdata` including `txdata[0]` (the coinbase). A coinbase output paying the multisig's P2TR script is returned from `scan_transaction` as a `ReceivedOutput`, yet per Bitcoin consensus it is immature and unspendable for 100 blocks.

### Finding Description
`scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` calls `self.scan_transaction(tx)` for every transaction in `block.txdata`, including the coinbase. `scan_transaction` (lines 199-214) matches only `output.script_pubkey` against registered scripts and returns a `ReceivedOutput` carrying the real `OutPoint`. There is no check for `tx.is_coinbase()` and no filtering of immature outputs. The only mitigation is a doc comment stating a "post-processing pass is needed," which the API does not enforce — any caller using the convenient `scan_block` entrypoint gets immature outputs as indistinguishable `ReceivedOutput`s.

A miner (an unprivileged party producing public transaction data) can pay the scanner's script_pubkey in their coinbase output. The output is then:
- reported as received funds,
- selectable as an input to `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs:150`, which has no maturity check,
- signed via the full FROST `TransactionMachine` flow, producing a transaction that consensus rejects (`bad-txns-premature-spend-of-coinbase`).

### Impact Explanation
Funds are reported received that are not spendable — the exact impact class targeted. Concretely: the wallet/multisig believes it received a confirmed, spendable output; a FROST signing round is executed committing nonces and signature shares to a transaction that can never be accepted while immature. Depending on the consumer's duplicate-output handling, the output may be permanently tracked as received-and-unspent while any spend attempt fails, causing accounting inflation and a signing-round stall.

### Likelihood Explanation
Reachable by any miner at negligible cost: include an output to the victim's known P2TR script_pubkey in a coinbase. It requires the coinbase payer to be a miner, which is realistic, and requires the consumer to use `scan_block` rather than manually skipping `txdata[0]` — a plausible misuse given the doc comment is the only defense. Medium rather than High since a motivated consumer can filter coinbases and the locked value becomes spendable after maturity.

### Recommendation
Skip the coinbase inside `scan_block` (`for tx in &block.txdata[1..]`), or mark `ReceivedOutput`s with a coinbase flag and have `SignableTransaction::new` reject immature coinbase outpoints (`OutPoint` ancestry isn't available, so tracking `is_coinbase` on `ReceivedOutput` at scan time is required).

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs — conceptual PoC
let scanner = Scanner::new(even_key).unwrap();
// Miner crafts a block whose coinbase pays scanner's script:
let coinbase = Transaction {
    // tx.is_coinbase() == true
    output: vec![TxOut { value: Amount::from_sat(100_000),
                         script_pubkey: p2tr_script_buf(even_key).unwrap() }],
    ..coinbase_tx
};
let block = Block { txdata: vec![coinbase], .. };
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1); // reported as received
// outputs[0] can be fed to SignableTransaction::new and signed via
// TransactionMachine, producing a tx consensus rejects for 100 blocks.
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
