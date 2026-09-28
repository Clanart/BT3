### Title
`Scanner::scan_block` reports immature coinbase outputs as received, inflating the spendable balance with outputs that cannot be spent for 100 blocks - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]` (the coinbase transaction) and emits any matching `ReceivedOutput` identically to normal outputs. Coinbase outputs are unspendable by consensus until they reach 100 confirmations, yet they are returned as ordinary spendable `ReceivedOutput`s. The reported received balance is therefore inflated by value that is not actually spendable, and any consumer that feeds these outputs into `SignableTransaction::new` will produce a transaction that is consensus-invalid and can never confirm.

### Finding Description
`scan_transaction` has no notion of coinbase status: it matches `output.script_pubkey` against `self.scripts` and pushes a `ReceivedOutput` for every match. `scan_block` calls it on every transaction in `block.txdata`, starting at index 0:

`networks/bitcoin/src/wallet/mod.rs:221-227`
```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

The only mitigation is a doc comment telling callers to post-process the results or use `block.txdata[1 ..]` (lines 216–220). Nothing in the returned `ReceivedOutput` (lines 89–97) marks it as immature; it exposes only `offset`, `output`, and `outpoint`, indistinguishable from a spendable output. The downstream constructor `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) accepts any `ReceivedOutput`, builds `TxIn`s from `input.outpoint` (lines 177–185), and produces a fully-signed transaction — which consensus will reject since `COINBASE_MATURITY` is enforced at the consensus layer, not the policy layer. This mirrors the Sherlock finding: an accounting entry (the received balance / `addressShares`) records value that was effectively removed (the slashed shares / the immature, not-yet-spendable coins), so the reported balance diverges from what can actually be used.

### Impact Explanation
An unprivileged party who can cause a coinbase payout to a scanned script (e.g., mining a block paying to a publicly known multisig address, or simply any miner payout to a monitored script) causes the wallet to report received funds that are not spendable. Consumers summing `ReceivedOutput::value()` see an inflated balance, and any spend attempt including that output produces a transaction that nodes reject (BIP-30/coinbase-maturity rule), causing failed broadcasts and potential liveness degradation if the unspendable output is repeatedly selected.

### Likelihood Explanation
Triggering requires a coinbase transaction paying to a scanned `script_pubkey`, which is achievable by anyone who can direct a block reward (miners, mining pools paying out to the address, or regtest-style environments). It requires no access to keys or privileged APIs — just on-chain data. The failure is deterministic once such a block is scanned.

### Recommendation
Skip `block.txdata[0]` inside `scan_block` (or tag `ReceivedOutput` with an `is_coinbase`/maturity flag) so coinbase outputs are never conflated with spendable outputs. If coinbase outputs must be tracked, only emit them once they satisfy the maturity window relative to the scanned chain tip.

### Proof of Concept
1. Construct a `Scanner` for an even `key` via `Scanner::new` (`mod.rs:162-166`).
2. Mine a block whose coinbase (`txdata[0]`) pays to `p2tr_script_buf(key)` — the in-repo test already does exactly this via `generatetoaddress` (`networks/bitcoin/tests/wallet.rs:43-69`).
3. `scanner.scan_block(&block)` returns a `ReceivedOutput` for the coinbase output with `offset() == Scalar::ZERO` and full `value()`.
4. Pass it to `SignableTransaction::new(...)` and sign; the resulting transaction spending `OutPoint { txid: <coinbase>, vout: 0 }` is consensus-invalid until 100 further confirmations, while the scanner reported it as an ordinary received output.