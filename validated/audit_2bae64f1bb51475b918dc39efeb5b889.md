### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable funds — callable on any block an unprivileged miner produced - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` is a public API that iterates `block.txdata` including `txdata[0]` (the coinbase transaction). Any `ReceivedOutput` it returns is treated downstream as a spendable, confirmed output, yet coinbase outputs are unspendable for 100 blocks under Bitcoin consensus. An unprivileged party who mines a block (or whose coinbase transaction pays the scanner's registered script) can cause the protocol to credit funds that cannot actually be spent.

### Finding Description
The analog to "callable from anyone with no restrictions producing unexpected protocol behavior" is `Scanner::scan_block` / `scan_transaction` in `networks/bitcoin/src/wallet/mod.rs:221`. `scan_block` loops over all transactions including the coinbase:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` (line 199) matches `output.script_pubkey` against `self.scripts` and emits a `ReceivedOutput` for any match, with no check on whether the containing transaction is a coinbase and no maturity metadata on the returned `ReceivedOutput` (`mod.rs:90-97` — only `offset`, `output`, `outpoint`). The caller has no way to distinguish an immature coinbase output from a normally spendable one without re-fetching the block and re-deriving tx position itself.

### Impact Explanation
A `ReceivedOutput` feeds the spend pipeline (`send.rs`) which assumes each returned outpoint is spendable. If the protocol attempts to spend an immature coinbase output, the transaction is consensus-invalid and rejected by the network — the funds are reported received but are not spendable. Depending on integrator accounting, this can inflate reported balances or stall signing rounds on an invalid transaction.

### Likelihood Explanation
Reachable by any unprivileged party able to get a transaction into a block paying a registered script — most concretely a miner whose coinbase output pays the vault's P2TR script. No key material, collusion, or privileged position is required beyond normal block production.

### Recommendation
In `scan_block`, skip `block.txdata[0]` or tag `ReceivedOutput` with coinbase/maturity status so callers cannot treat immature outputs as spendable. At minimum, enforce in `scan_transaction` a flag indicating coinbase origin rather than relying solely on the doc comment (line 218-220) for callers to perform the post-processing pass.

### Proof of Concept
1. Miner mines a block whose coinbase tx pays `script_pubkey` matching a registered `Scanner` script.
2. Integrator calls `Scanner::scan_block(&block)`; the coinbase output is returned as a `ReceivedOutput` indistinguishable from a normal payment.
3. `send.rs` builds a spend consuming that outpoint; the resulting transaction is rejected by Bitcoin consensus rules (coinbase maturity < 100 blocks) even though the scanner reported the funds as received and spendable.

Relevant code: `networks/bitcoin/src/wallet/mod.rs:199-227`.