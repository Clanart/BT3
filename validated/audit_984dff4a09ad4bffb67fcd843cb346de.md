### Title
`Scanner::scan_block` reports immature coinbase outputs as received, though they are unspendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to `getLastestPrice` consuming oracle data without checking `updatedAt` freshness, `Scanner::scan_block` consumes block data without checking whether a matched output is mature/spendable. It unconditionally scans `block.txdata[0]` (the coinbase transaction) and emits a `ReceivedOutput` for it. A coinbase output cannot be spent for 100 blocks, so the scanner reports funds as received that are not actually spendable.

### Finding Description
`scan_transaction` matches any output whose `script_pubkey` is registered in `self.scripts`, producing a `ReceivedOutput` that downstream code treats as a spendable input to `SignableTransaction::new`. `scan_block` iterates over *all* of `block.txdata`, including `txdata[0]` (the coinbase), with no check on coinbase maturity:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

Bitcoin consensus forbids spending a coinbase output until it has 100 confirmations. Just as the Y2K controller trusted `latestRoundData` without validating `updatedAt`, `scan_block`/`scan_transaction` trust an output's presence in a block without validating its spendability status. There is no height/maturity field on `ReceivedOutput`, so nothing downstream can even detect the condition — `SignableTransaction::new` sums `input.output.value` and constructs `TxIn`s assuming every input is spendable, and `TransactionSignMachine::sign` produces a fully signed, invalid transaction spending an immature coinbase.

### Impact Explanation
A miner (or anyone who can get a coinbase paying a registered script into a scanned block) causes funds to be reported received that are not spendable. If the receiving system credits or attempts to spend such an output, the constructed transaction is consensus-invalid and will be rejected by the network — a permanent accounting mismatch (funds counted as available that cannot move for ~100 blocks), or wasted threshold-signing rounds on a transaction that can never confirm.

### Likelihood Explanation
Any mined block whose coinbase pays to a scanned script triggers this. This requires a miner to pay Serai's address, which is plausible (e.g., a mining pool configured to pay the multisig address, or a deliberate dust payment to a known address). Reachable purely from public on-chain data; no privileged access needed.

### Recommendation
In `scan_block`, skip `block.txdata[0]` or check `tx.is_coinbase()` and exclude/mark its outputs, rather than documenting that callers must post-process. Alternatively, record the containing block's coinbase status on `ReceivedOutput` and have `SignableTransaction::new` reject immature inputs. The processor already works around this by scanning `block.txdata[1..]` in `get_outputs`, but the library API itself remains unsafe by default.

### Proof of Concept
1. A miner mines a block whose coinbase transaction (`block.txdata[0]`) contains an output paying `p2tr_script_buf(scanner_key)` (a registered script).
2. Any caller invokes `scanner.scan_block(&block)`. The loop over `&block.txdata` includes index 0, and `scan_transaction` matches the coinbase output's `script_pubkey`, returning a `ReceivedOutput`.
3. The caller passes the `ReceivedOutput` to `SignableTransaction::new`, which builds a `TxIn` referencing the coinbase outpoint, and the FROST `TransactionMachine` signs it successfully.
4. Broadcasting the signed transaction fails consensus validation (`bad-txns-premature-spend-of-coinbase`), even though the scanner reported the funds as received.