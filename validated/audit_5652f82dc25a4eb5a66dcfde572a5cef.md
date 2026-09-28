### Title
Scanner reports immature coinbase outputs as spendable `ReceivedOutput`s - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` iterates over every transaction in a block, including `block.txdata[0]` (the coinbase). Any coinbase output paying to a scanned script is returned as a normal `ReceivedOutput`, indistinguishable from a spendable UTXO, even though coinbase outputs are unspendable for 100 blocks by consensus.

### Finding Description
The external report describes a permissionless `receive()` allowing anyone to push native assets into a contract that cannot recover them — unrequested inbound value becomes stuck. The Serai analog lives in the Bitcoin wallet scanner. `scan_transaction` matches outputs purely by `script_pubkey`, which is by design for a deposit scanner — anyone may permissionlessly send BTC to the multisig's addresses. However, `scan_block` applies this matching to the coinbase transaction as well:

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

There is no `is_coinbase()`/maturity check anywhere in `networks/bitcoin/src` (the only mention is the doc comment on `scan_block`). A `ReceivedOutput` produced from a coinbase output carries only `offset`, `output`, and `outpoint` — nothing marks it as immature. Any miner can therefore, permissionlessly and without any cooperation, cause the scanner to emit a `ReceivedOutput` that looks fully spendable but whose spend would be consensus-invalid for 100 blocks.

### Impact Explanation
Downstream consumers treat every `ReceivedOutput` as a spendable input. `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` performs no maturity validation, so a wallet built directly on this crate (the in-scope, supported consumer surface) would construct and FROST-sign a transaction spending an immature coinbase output. That transaction is rejected by the network, yet the scanner has already recorded the output as received — funds are reported received that are not spendable, matching the "stuck funds" impact class of the source finding (here: temporarily stuck/unspendable, plus wasted signing rounds and potential repeated attempts).

### Likelihood Explanation
Low-to-moderate. It requires a miner (or a pool) to craft a coinbase paying a Serai-watched P2TR script — not typical, but fully permissionless and cheap for any miner, and reachable purely through public on-chain data with no privileged access. Notably, `processor/src/networks/bitcoin.rs` `get_outputs` explicitly skips `block.txdata[0]`, confirming the in-tree consumer had to special-case this hazard — evidence the default `scan_block` behavior is unsafe rather than a theoretical concern.

### Recommendation
In `Scanner::scan_block`, either skip `block.txdata[0]` by default or record coinbase-derived outputs with a maturity flag/block-height so callers cannot spend them before maturity. At minimum, make the immaturity machine-readable on `ReceivedOutput` rather than relying on a doc comment.

### Proof of Concept
1. Construct a `Scanner` via `Scanner::new(key)` for any even-P2TR-able key.
2. Mine a regtest block whose coinbase output pays `p2tr_script_buf(key)` (exactly what `networks/bitcoin/tests/wallet.rs:40-52` does via `generatetoaddress`).
3. Call `scanner.scan_block(&block)` — it returns a `ReceivedOutput` for the coinbase output (the test asserts `scan_block` output equals `scan_transaction(&block.txdata[0])`, proving coinbase inclusion).
4. Feed that `ReceivedOutput` to `SignableTransaction::new` — it is accepted as an input, yet spending it is consensus-invalid until 100 confirmations elapse.