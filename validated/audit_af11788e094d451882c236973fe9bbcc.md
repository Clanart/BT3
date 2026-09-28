### Title
`Scanner::scan_block` treats coinbase outputs as spendable inputs, letting a miner poison the input set with immature outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Ubiquity report's bug class is a fragile invariant (`_reserve0 == _reserve1`) placed on attacker-influenceable state, which an unprivileged party can break via a public action to break a core function. The analogous pattern in `bitcoin-serai` is `Scanner::scan_block`, which assumes every transaction output paying to a registered `script_pubkey` is a usable input. Any miner — an unprivileged party with only public knowledge of the Serai P2TR address — can include an output to that address in the block's coinbase transaction. `scan_block` iterates `block.txdata` including `txdata[0]` (the coinbase), so this output is returned as a `ReceivedOutput` indistinguishable from a spendable one [1](#0-0) . Coinbase outputs are consensus-immature for 100 blocks, so the "funds received" cannot actually be spent.

### Finding Description
`Scanner::scan_transaction` matches outputs purely by `script_pubkey` equality against the registered script set [2](#0-1) . `scan_block` feeds it every transaction in the block, including the coinbase, whose outputs are bound by the 100-block maturity rule [3](#0-2) . The code comment acknowledges this ("bound by maturity... a post-processing pass is needed") but no such pass exists in the crate, and `ReceivedOutput` carries no flag distinguishing coinbase-sourced outputs [4](#0-3) . Downstream, `SignableTransaction::new` happily consumes any `ReceivedOutput` as a `TxIn` [5](#0-4) , producing a transaction that is consensus-invalid (BIP-30/COINBASE_MATURITY) and can never confirm.

### Impact Explanation
Like the original report, an external party breaks an implicit invariant on shared state to break a core function. A miner (or pool) sending even a dust coinbase output to the Serai key's script causes scanners to report unspendable funds. If the processor selects that output as an input, the resulting `SignableTransaction` is signed and broadcast but permanently rejected — wasting a threshold-signing session and stalling payments that include the poisoned input. This is "funds reported received that are not spendable," an accepted impact class.

### Likelihood Explanation
Requires the attacker to mine a block (or control a pool's coinbase template) — not free, but an unprivileged external party. No validator/peer collusion needed. Impact is availability of spend transactions until the output matures or is filtered.

### Recommendation
In `Scanner::scan_block`, skip `block.txdata[0]` (or pass an `is_coinbase` flag through `scan_transaction`) and mark coinbase-derived `ReceivedOutput`s as immature, exposing a `mature_at`/`is_immature` accessor so callers can exclude them from input selection.

### Proof of Concept
1. Register `Scanner::new(key)` for a tweaked Serai key.
2. Attacker mines a block whose coinbase `txdata[0]` contains `TxOut { value: dust_or_more, script_pubkey: p2tr_script_buf(key) }`.
3. `scan_block(&block)` returns a `ReceivedOutput` for the coinbase vout with `offset == Scalar::ZERO` [2](#0-1) .
4. `SignableTransaction::new(vec![that_output], payments, change, None, fee)` succeeds [6](#0-5) ; the signed transaction is rejected by every node because the coinbase spend is before maturity — the spend is bricked exactly as `setPool` was bricked in the original report.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-169)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
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
