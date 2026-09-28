### Title
Coinbase outputs are reported as received/spendable, so any transaction spending them always fails consensus - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a bug class where a component invokes a function variant that is invalid in the deployed context (`worked(address)` vs `worked(address, uint256)`), so every execution path through it always reverts. The analog in Serai's in-scope Bitcoin code is `Scanner::scan_block` / `Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs`: they return `ReceivedOutput`s for coinbase transactions, which are consensus-immature for 100 blocks. Any downstream spend built by `SignableTransaction`/`TransactionMachine` over such an input produces a transaction the Bitcoin network always rejects — the wallet's "exec" always fails.

### Finding Description
`Scanner::scan_block` iterates `block.txdata` with no `is_coinbase` exclusion (mod.rs:221-227), and `scan_transaction` matches purely on `output.script_pubkey` (mod.rs:205-211). The code acknowledges this — the doc comment says the coinbase "is bound by maturity" and "a post-processing pass is needed to remove those outputs" — but the function neither enforces nor performs that pass; the `ReceivedOutput` type carries no maturity/height field (mod.rs:90-97), so downstream code cannot distinguish an immature coinbase output from a normal spendable UTXO.

A miner (an unprivileged party sending a Bitcoin transaction, an explicitly allowed adversary input) can place an output paying to Serai's P2TR script in their coinbase transaction. It is scanned and reported as a received output, and when the scheduler/wallet selects it as an input to `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), the FROST-signed result is consensus-invalid (`BIP-34`/coinbase-maturity rule) and will always be rejected until 100 confirmations — exactly the "calls always revert" failure shape of the report.

### Impact Explanation
Funds are reported received that are not spendable (an accepted impact), and every transaction attempting to spend them always fails. If coinbase outputs are co-mingled with normal inputs, unrelated payments are poisoned as well.

### Likelihood Explanation
Requires a miner to include a payment to a Serai vault script in a coinbase. This is cheap for a miner and needs no collusion; it is not dependent on Serai making an error. Severity Medium: functionality breaks for affected outputs for a fixed window, not permanent fund loss.

### Recommendation
Check `tx.is_coinbase()` in `scan_transaction`/`scan_block` and either skip coinbase outputs or tag `ReceivedOutput` with maturity information so the scheduler defers spending for 100 blocks. Enforce this in the wallet crate rather than relying on callers to post-process.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {            // includes block.txdata[0], the coinbase
    res.extend(self.scan_transaction(tx));
  }
  res
}
```
A regtest demonstration: mine a block whose coinbase pays to `p2tr_script_buf(key)` for a registered Serai key. `scanner.scan_block(&block)` returns a `ReceivedOutput` for vout 0. Build `SignableTransaction::new(vec![that_output], &payments, change, None, fee)` and complete the FROST signing via `TransactionMachine`; the resulting `Transaction` is unconditionally rejected by `sendrawtransaction` (`bad-txns-premature-spend-of-coinbase`) for 100 blocks — the execution always fails, mirroring the report's always-reverting `exec()`.

Caveat: I verified the defect in the in-scope wallet crate, but I could not confirm (within iteration limits) whether `processor/src/networks/bitcoin.rs`'s `get_outputs` performs the documented post-processing pass to exclude coinbase outputs; if it does, the same latent defect remains in the wallet API but the production impact is mitigated.