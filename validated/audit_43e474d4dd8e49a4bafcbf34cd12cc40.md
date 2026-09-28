### Title
`Scanner::scan_block` reports immature coinbase outputs as received, making unspondable funds appear spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]` (the coinbase transaction) and returns any outputs paying to a registered script as ordinary `ReceivedOutput`s. Coinbase outputs are consensus-unspendable for 100 blocks, yet they are reported identically to normal, immediately-spendable outputs. The production caller in `processor/src/networks/bitcoin.rs` works around this by explicitly slicing `block.txdata[1 ..]`, but the in-scope wallet API itself performs no maturity filtering, so any consumer of `scan_block` (the documented primary entry point for block scanning) receives unspondable funds reported as received.

### Finding Description
`scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches `output.script_pubkey` against the registered script map and emits a `ReceivedOutput` carrying the spend offset, the `TxOut`, and the outpoint. `scan_block` (networks/bitcoin/src/wallet/mod.rs:221-227) calls it on every transaction in `block.txdata`, including index 0, the coinbase. The only mitigation is a doc comment stating that "a post-processing pass is needed" or that callers should invoke `scan_transaction` on `block.txdata[1 ..]` themselves. The API makes the immature coinbase output indistinguishable from a spendable output: same type, valid offset, valid outpoint. This mirrors CVE-2019-15591's shape: a secondary data path (`scan_block` / the merge-request widget) returns data that the primary path restricts — the processor's `get_outputs` (processor/src/networks/bitcoin.rs:691) deliberately skips `txdata[0]`, proving the restriction exists in principle but is not enforced in the library entry point.

### Impact Explanation
An unprivileged party who mines a block (no permission required beyond producing a valid block, which any miner can attempt) can place an output paying to a registered multisig script inside the coinbase transaction. Any wallet/processor using `Scanner::scan_block` will report those funds as received with a spendable-looking `ReceivedOutput`. The funds cannot actually be spent for 100 blocks (and vanish entirely if the block is orphaned), so funds are reported received that are not spendable. A downstream consumer constructing a `SignableTransaction` over such an output would produce a transaction consensus-rejected as immature, and accounting built on scan results overstates the spendable balance — including crediting deposits that may never mature if the block is reorganized out.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the attacker to mine a block and to know a registered script (external deposit addresses are public-ish to depositors), and the impact depends on a consumer calling `scan_block` directly rather than `get_outputs`, which already skips the coinbase. It is, however, fully reachable from public inputs — the coinbase is attacker-supplied transaction data — and requires no key material, no validator collusion, and no misuse of internals beyond calling the public scanning API as documented for whole blocks.

### Recommendation
Filter the coinbase transaction inside `scan_block` itself (iterate `block.txdata[1 ..]`, guarded for empty blocks), or return a flagged `ReceivedOutput` variant/`is_coinbase` marker so maturity can be enforced by the caller. Alternatively, add a `min_confirmations`/maturity-aware scanning path so a coinbase output cannot be returned as a plain spendable output.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs — scan_block has no maturity guard
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {          // includes txdata[0], the coinbase
    res.extend(self.scan_transaction(tx));
  }
  res
}
```
A miner builds a block whose coinbase pays `value` to `ScriptBuf::new_p2tr_tweaked(...)` matching a script registered via `Scanner::new(key)` / `register_offset`. Calling `scanner.scan_block(&block)` returns a `ReceivedOutput { offset, output, outpoint }` indistinguishable from a confirmed spendable deposit, while `processor/src/networks/bitcoin.rs:691` shows the correct behavior (`for tx in &block.txdata[1 ..]`) that the library API itself does not enforce.