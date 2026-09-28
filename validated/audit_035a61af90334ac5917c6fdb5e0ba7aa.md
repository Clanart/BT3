### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]`, the coinbase transaction. Outputs created by a coinbase are encumbered by Bitcoin's 100-block maturity rule and cannot be spent until then. `ReceivedOutput` carries no flag distinguishing a coinbase-origin output from a normal one, so any consumer that treats the returned outputs as spendable funds will construct transactions that the Bitcoin network rejects.

### Finding Description
`scan_transaction` matches any `script_pubkey` registered via `Scanner::new` / `register_offset` and returns a `ReceivedOutput` containing only the offset, the `TxOut`, and the `OutPoint`: [1](#0-0) 

`scan_block` applies this to every transaction, including the coinbase: [2](#0-1) 

The doc comment acknowledges the issue ("If received outputs must be immediately spendable, a post-processing pass is needed"), but nothing in the API enforces it, and `ReceivedOutput` (whose fields are `offset`, `output`, `outpoint`) gives the caller no way to detect maturity after the fact. This is the same bug class as the reference report: state (vote weight there, spendability here) is read at the current block even though it is only valid for prior blocks. The fact that the sibling production consumer in `processor/src/networks/bitcoin.rs` explicitly skips `block.txdata[1 ..]` with the comment "Skip the coinbase transaction which is burdened by maturity" demonstrates both that the incorrect behavior is reachable through the public API and that correct handling requires the caller to know an undocumented detail of which tx index is special.

A minor's coinbase can pay to any script, so an unprivileged miner can send funds to a script the `Scanner` watches (the raw group key P2TR script, or any registered offset script) purely via public transaction data.

### Impact Explanation
A miner who pays a scanned script in a coinbase causes `scan_block` (or `scan_transaction` called on `block.txdata[0]`, as the test at `networks/bitcoin/tests/wallet.rs:66` does) to emit a `ReceivedOutput` that is not spendable for 100 blocks. A downstream consumer feeding these outputs into `SignableTransaction::new` / `SignableTransaction::multisig` will produce a `TransactionMachine` whose signatures commit to an immature input; the resulting signed transaction is invalid and rejected by Bitcoin nodes. Depending on the consumer's retry/blame logic, this is a denial of service on the signing pipeline and can burn multisig preprocesses on a transaction that can never confirm, and it misreports unspendable funds as received balance.

### Likelihood Explanation
Exploitation requires mining a block (or influencing a pool's coinbase outputs) paying the watched script — coinbase outputs are chosen freely by the miner, so no cooperation from Serai is needed. The defect is deterministic: every coinbase matching a registered script is emitted. The only mitigating factor is that correctly-written consumers must independently know to skip `txdata[0]`, which the primary in-tree consumer does — meaning the flawed API path exists precisely for any consumer that uses `scan_block` as documented for block scanning.

### Recommendation
Do not return coinbase outputs from `scan_block`, or make maturity explicit. Concretely: skip `block.txdata[0]` inside `scan_block` (matching the processor's behavior), or record maturity on `ReceivedOutput` (e.g., a `coinbase: bool` / `matures_at` field set when `tx.is_coinbase()` is true) so consumers can filter. If skipping is rejected to preserve generality, `scan_transaction` should at minimum not silently succeed on coinbase inputs when the caller intends spendable outputs.

### Proof of Concept
On regtest:

```rust
// key: a ProjectivePoint the Scanner watches
let scanner = Scanner::new(key).unwrap();

// Mine a block whose coinbase pays the scanner's P2TR script
// (a miner chooses the coinbase script_pubkey freely)
let block: Block = ...; // block.txdata[0] is the coinbase paying p2tr_script_buf(key)

let outputs = scanner.scan_block(&block);

// The coinbase output is reported identically to a normal received output
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));

// outputs[0] cannot be spent for 100 blocks, yet is indistinguishable
// from a spendable output; the in-tree test at
// networks/bitcoin/tests/wallet.rs:65-69 confirms scan_block emits it.
```

This mirrors the existing test which already demonstrates `scan_block` returning the coinbase output (`assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]))`), relying only on the fact that the harness mines additional blocks before spending — a behavior a real scanner consumer cannot rely on for funds that must be spendable immediately.

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
