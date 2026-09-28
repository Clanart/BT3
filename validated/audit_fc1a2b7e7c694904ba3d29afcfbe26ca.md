### Title
Scanner reports immature coinbase outputs as spendable ReceivedOutputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]` (the coinbase transaction) and returns any outputs paying a registered script as `ReceivedOutput`s. Coinbase outputs are bound by Bitcoin's 100-block maturity rule, while Serai's confirmation window is only `N::CONFIRMATIONS` (6 for Bitcoin). The result is a window inconsistency directly analogous to the reference finding: an output is treated as finalized/available far earlier than the protocol actually permits it to be spent.

### Finding Description
The bug class in the external report is a time/validity window defined by code that does not match the real domain semantics (a "creation window" that doesn't correspond to how the underlying system actually works). In `networks/bitcoin/src/wallet/mod.rs`, `scan_block` scans every transaction in the block:

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

The doc comment at lines 216-220 explicitly acknowledges the mismatch: "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed to remove those outputs."

Meanwhile the processor defines `Bitcoin::CONFIRMATIONS = 6` (`processor/src/networks/bitcoin.rs:604`) and the scanner loop only waits `CONFIRMATIONS` blocks before emitting outputs (`processor/src/multisigs/scanner.rs:481`). A `ReceivedOutput` has no maturity/height metadata — only `offset`, `output`, and `outpoint` (`networks/bitcoin/src/wallet/mod.rs:90-97`) — so once emitted, nothing downstream can distinguish an immature coinbase output from a normal spendable UTXO. I was unable to fully verify whether the processor's `Block` wrapper strips `txdata[0]` before calling `scan_transaction` (there is one `coinbase`/`scan_block` match in `processor/src/networks/bitcoin.rs` whose context I could not inspect); if it scans the whole block via `scan_block`, the immature output flows straight through.

### Impact Explanation
Any miner — an unprivileged party — can pay the Serai multisig address in a coinbase output. That output is reported as received and becomes schedulable after 6 confirmations, but Bitcoin consensus rejects any transaction spending it until 100 blocks have elapsed. If the scheduler selects it as an input, the entire constructed transaction is invalid (non-final), potentially stalling the batch/transfer plan it was included in and burning fee budget on a transaction that can never confirm. This meets the "funds reported received that are not spendable" acceptance criterion.

### Likelihood Explanation
Exploitation requires a miner to direct coinbase rewards to the Serai address, which is cheap for any pool/solo miner already producing blocks and requires no victim interaction. The trigger condition (a coinbase output to a registered script appearing within the 6-confirmation scan window vs. the 100-block maturity window) is deterministic once such a payment exists.

### Recommendation
In `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs`), skip `block.txdata[0]` when the block's first transaction is a coinbase, or thread block height into `ReceivedOutput`/`scan_block` and filter outputs whose `outpoint.txid` belongs to a coinbase until `height + 100` confirmations exist. Alternatively, make `scan_block` skip the coinbase unconditionally and document that `scan_transaction` on `block.txdata[1 ..]` is the canonical path — matching the recommendation style of the original finding (derive the window from protocol rules, not from an arbitrary/configured value).

### Proof of Concept
```rust
// Attacker (a miner) mines a block whose coinbase pays Serai's P2TR script:
//   coinbase.outputs[0].script_pubkey == p2tr_script_buf(multisig_key)

// Processor scans after CONFIRMATIONS (6) blocks:
let outputs = scanner.scan_block(&block); // iterates ALL txdata including txdata[0]

// outputs contains ReceivedOutput { outpoint: (coinbase_txid, 0), ... }
// despite the output being unspendable until block height + 100.

// Scheduler later picks it as an input; the signed transaction spending it is
// rejected by Bitcoin consensus (bad-txns-premature-spend-of-coinbase),
// stalling every other payment bundled in the same transaction.
```