### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable received funds — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` in its entirety, including `txdata[0]` — the coinbase transaction. Coinbase outputs are encumbered by Bitcoin's 100-block maturity rule: they appear on-chain and match a registered `script_pubkey`, yet any transaction spending them is consensus-invalid until maturity. The scanner emits them as ordinary `ReceivedOutput`s with no flag, maturity check, or filtering — an analog to transferring/using an asset whose blocked/restricted status was never checked.

### Finding Description
`scan_transaction` matches outputs purely by `script_pubkey` against `self.scripts` and builds a `ReceivedOutput` for each match (`networks/bitcoin/src/wallet/mod.rs:199-214`). `scan_block` then calls it on every transaction including the coinbase (`mod.rs:221-227`):

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {          // txdata[0] is the coinbase
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

The only mitigation is a doc comment stating "a post-processing pass is needed" — the API itself returns immature, unspendable outputs indistinguishable from spendable ones. `ReceivedOutput` carries no maturity/confirmed-spendable field, so downstream code (or any integrator feeding these into `SignableTransaction::new`) receives "funds reported received that are not spendable". A `SignableTransaction` built on such an input produces a transaction that will be rejected by the network (BIP-113/consensus coinbase-maturity rule), burning signer rounds and potentially stalling a signing plan — and the offset/script gives no hint the input was coinbase-sourced.

### Impact Explanation
Any miner can send a coinbase output to a registered Serai multisig script (the external address is public). `scan_block` will report it as a normal received output. If consumed, the resulting spend transaction is consensus-invalid for 100 blocks, causing failed signing attempts and misaccounted balance. The library's own producer (`processor`'s `get_outputs`) has to manually skip `block.txdata[1 ..]` — evidence the raw API misreports, and any consumer that doesn't replicate that workaround is affected.

### Likelihood Explanation
Mining a block paying to the multisig's P2TR script is permissionless; merged/auxiliary mining or a miner depositing directly makes this reachable without coordination. Every coinbase is guaranteed to trigger the path whenever it pays to a watched script.

### Recommendation
Have `scan_block` skip `block.txdata[0]` (or mark `ReceivedOutput` with a maturity flag and filter immature coinbases), rather than relying on every caller to post-process. Alternatively, return a distinct output kind for coinbase outputs so consumers can't silently treat them as spendable.

### Proof of Concept
```rust
// Regtest: mine a block whose coinbase pays to the multisig's script.
let block = rpc.get_block(&rpc.get_block_hash(n).await.unwrap()).await.unwrap();
// txdata[0] is the coinbase paying to p2tr_script_buf(key)
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput for the coinbase — reported as received,
// yet spending it in SignableTransaction produces a consensus-invalid TX
// (coinbase maturity), and nothing in ReceivedOutput distinguishes it.
```
The in-repo test `send_and_get_output` (`networks/bitcoin/tests/wallet.rs:40-78`) demonstrates exactly this: it scans `block.txdata[0]`'s coinbase via `scan_block` and only succeeds because the test pre-mines 100 maturity blocks — confirming `scan_block` itself performs no maturity check.