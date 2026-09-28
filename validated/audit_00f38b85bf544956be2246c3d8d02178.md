### Title
`Scanner::scan_block` credits immature coinbase outputs the multisig cannot spend - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The Bitcoin `Scanner` iterates `block.txdata` in full, including `txdata[0]` (the coinbase transaction). Any miner — a permissionless role — can mine a block whose coinbase pays to the multisig's P2TR `script_pubkey`. The scanner will emit a `ReceivedOutput` for it, yet coinbase outputs are unspendable for 100 blocks by consensus. The multisig's scheduler will then plan a spend including an input that makes the entire transaction consensus-invalid, so the output is reported received but is not spendable.

### Finding Description
`Scanner::scan_block` calls `scan_transaction` on every transaction in the block with no exclusion of the coinbase transaction:

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

`scan_transaction` matches purely on `output.script_pubkey` membership in `self.scripts` (`networks/bitcoin/src/wallet/mod.rs:199-214`), so a coinbase output paying to the tweaked multisig key is indistinguishable from an ordinary deposit. The code itself acknowledges the hazard in the doc comment ("This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed to remove those outputs", lines 216-220), but no such filtering is performed inside the wallet crate, and `ReceivedOutput` carries no flag letting downstream code distinguish coinbase-derived outpoints.

This is the Serai analog of the Vader `rescue` report's bug class: value that lands on the controlled address but cannot actually be moved under the protocol's rules is silently folded into the spendable balance. Where Vader's unaccounted tokens were siphoned by anyone, here the "unaccounted" coinbase output is credited to the multisig while being consensus-unspendable, poisoning the UTXO set the scheduler plans from.

### Impact Explanation
Once scanned, the immature output is pushed into the multisig's output set (the processor's scanner emits `ScannerEvent::Block` outputs, which the scheduler consumes as UTXOs — `processor/src/multisigs/scanner.rs:562-567`, `processor/src/multisigs/scheduler/utxo.rs:411-431`). Any `Plan`/signing round that selects this input produces a transaction violating BIP-30/COINBASE_MATURITY: the transaction can never enter a mempool or block, so the associated payments stall and the signing round's inputs remain locked. Repeated or batch inclusion of the poisoned output can halt an entire multisig's payout flow until the output matures and plans are rebuilt. Severity: Medium — no permanent theft, but externally triggerable loss of liveness and "received but unspendable" accounting.

### Likelihood Explanation
The trigger requires mining a block, which is permissionless but costly. A miner (or mining pool) directing its coinbase to a known Serai multisig P2TR address forgoes nothing extra beyond the normal reward destination — pool payouts to arbitrary addresses are routine — so any block mined by a pool that pays out to a monitored script, deliberately or accidentally, injects immature outputs into the scheduler. Because `scan_transaction` has no maturity check, no user-level transaction is even needed; ordinary mining activity suffices.

### Recommendation
In `Scanner::scan_block`, skip `block.txdata[0]` or check `tx.is_coinbase()` and exclude/defer those outputs; alternatively tag `ReceivedOutput` with a maturity height and have the scheduler ignore inputs younger than `COINBASE_MATURITY`. Consumers should prefer the documented `block.txdata[1..]` behavior unconditionally.

### Proof of Concept
1. Observe the multisig's P2TR `script_pubkey` (from `p2tr_script_buf` of the tweaked group key, `networks/bitcoin/src/wallet/mod.rs:80-86`, registered in `Scanner::new` at line 164).
2. Mine a regtest/mainnet block whose coinbase transaction has an output paying directly to that `script_pubkey`.
3. Call `Scanner::scan_block(&block)`: it returns a `ReceivedOutput` for the coinbase outpoint, since matching is script-only.
4. The scheduler treats the output as a normal UTXO and builds a spend `Plan` (`processor/src/multisigs/scheduler/utxo.rs:423-431`). The resulting transaction includes a `<100`-confirmations coinbase input and is rejected by every Bitcoin node (`bad-txns-premature-spend-of-coinbase`), despite Serai's books showing the balance as received and spendable.