### Title
`Scanner::scan_block` reports coinbase outputs as received despite them being immature and unspendable — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a proposer submitting an output without the L1 block-hash anchor that guards against reorgs, so data accepted as valid becomes invalid if the chain reorganizes. The analog in `bitcoin-serai` is `Scanner::scan_block`, which feeds every transaction in a block — including the coinbase transaction — through `scan_transaction`, producing `ReceivedOutput`s for coinbase outputs that are not spendable for 100 blocks and vanish entirely if the block is reorganized out or matured differently. There is no check inside the scanner that a matched output is actually spendable or finalized; an unprivileged miner can cause the scanner to report funds received that cannot be spent.

### Finding Description
`Scanner::scan_transaction` matches outputs purely on `script_pubkey` against the registered script set (`self.scripts.get(&output.script_pubkey)`), with no notion of what kind of transaction carried the output. `scan_block` then iterates `block.txdata` starting at index 0, i.e. including the coinbase:

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

The doc comment acknowledges the problem — "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed" — but the API returns the immature output as an ordinary `ReceivedOutput` indistinguishable from a spendable one. The downstream consumer in the processor had to work around this by slicing `block.txdata[1 ..]` in `get_outputs`, confirming the coinbase path is reachable and produces results the rest of the system cannot spend. A miner (or any party able to get a coinbase included in a scanned block) can send the block reward to the scanner's P2TR script; the output is reported as received while consensus rules forbid spending it for 100 blocks, and a reorg removes it outright.

### Impact Explanation
Funds are reported received that are not spendable — an output kind explicitly accepted as in-scope impact. If a consumer treats the `ReceivedOutput` from `scan_block` as a usable input (e.g., schedules it into a `Plan`/signing session), the resulting transaction is consensus-invalid (`bad-txns-premature-spend-of-coinbase`), burning fee work and potentially stalling a signing round. Under a reorg of the scanned block the reported outpoint ceases to exist entirely, mirroring the external report where a missing finality anchor produces an invalid commitment.

### Likelihood Explanation
Any miner can trigger it with a valid coinbase — no privilege or collusion required, and the triggering input (a block containing a coinbase paying the multisig's script) is public transaction data, which is the reachable-input class allowed by scope. Exploitation requires only that a caller uses `scan_block` (the natural whole-block API) rather than manually slicing off `txdata[0]`. Severity is Medium: no secret leakage or forgery, but false accounting of spendable funds and potential invalid-spend attempts.

### Recommendation
Filter the coinbase transaction inside `scan_block` itself (skip `block.txdata[0]`, or check `tx.is_coinbase()`/`input[0].previous_output.is_null()` in `scan_transaction` when invoked on block data), or return a distinct variant on `ReceivedOutput` marking immaturity so callers cannot mistake it for a spendable output. At minimum, do not silently return coinbase-derived outputs through the same type as confirmed, spendable ones.

### Proof of Concept
1. Construct a `Scanner` via `Scanner::new(key)` for a scanner key `key` with an even-Y P2TR script.
2. Build a regtest `Block` whose `txdata[0]` is a coinbase transaction with `output[0].script_pubkey = p2tr_script_buf(key)` and add a second normal transaction paying the same script.
3. Call `scanner.scan_block(&block)` — it returns two `ReceivedOutput`s, one with `outpoint` referencing the coinbase txid.
4. Any attempt to spend that first `ReceivedOutput` produces a transaction rejected by consensus (`coinbase` maturity, 100 blocks), and after a reorg of `block` the outpoint is invalid entirely — demonstrating funds reported as received that are not spendable.

Relevant code: `networks/bitcoin/src/wallet/mod.rs` lines 198–227 (`scan_transaction`, `scan_block`), and the downstream workaround at `processor/src/networks/bitcoin.rs` line 691 (`for tx in &block.txdata[1 ..]`).