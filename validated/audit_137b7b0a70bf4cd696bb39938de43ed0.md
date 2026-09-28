### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The UBIFS bug class is "marking data valid/uptodate before its contents are finalized, so a reader observes data that isn't actually usable yet." The direct analog in `bitcoin-serai` is `Scanner::scan_block`, which marks a `ReceivedOutput` — a type defined as "a spendable output" — as received/spendable before the output's spendability precondition (coinbase maturity) is satisfied. Outputs paid to a Serai script inside a block's coinbase transaction are returned identically to ordinary, immediately spendable outputs.

### Finding Description
`Scanner::scan_transaction` matches `output.script_pubkey` against registered scripts and emits a `ReceivedOutput` with no further validation. `Scanner::scan_block` iterates `block.txdata` in full, explicitly including `txdata[0]`, the coinbase transaction (networks/bitcoin/src/wallet/mod.rs:221-227). Bitcoin consensus rules make coinbase outputs unspendable for 100 blocks (`COINBASE_MATURITY`), but nothing in the `ReceivedOutput` type or the scan path records maturity status — the only mitigation is a doc comment telling callers to either post-process the results or call `scan_transaction` on `block.txdata[1 ..]` themselves.

An attacker who mines a block (any miner/pool, an unprivileged position reachable purely by producing public transaction data) can place an output paying Serai's scanned script inside the coinbase. Any downstream consumer that treats `scan_block` results as spendable — the exact semantics the type name and `ReceivedOutput` docs declare — will plan/sign a transaction spending an immature outpoint. The resulting transaction is consensus-invalid and will be rejected by every Bitcoin node, stalling the associated plan until the attempt machinery gives up, and repeatedly failing on re-broadcast since the plan references a permanently-tainted input until maturity elapses.

### Impact Explanation
Funds reported received that are not spendable. An immature coinbase output entering the payment pipeline causes the constructed transaction to be invalid at consensus level, producing a failed/stuck plan for the affected inputs — a liveness/integrity fault on funds handling reachable with public inputs (a crafted block the attacker mines).

### Likelihood Explanation
Requires the attacker to mine a block and deliberately pay the Serai script in the coinbase — costly but fully within the threat model of "Bitcoin transactions they send / cause to be scanned." It is also reachable accidentally: any merge-mining pool or miner adding arbitrary outputs. Severity is bounded (delay, not theft), so Medium.

### Recommendation
Skip `block.txdata[0]` inside `scan_block`, or carry maturity metadata on `ReceivedOutput` (e.g., a `maturity: Option<u64>` or a distinct output kind) so callers cannot confuse an immature coinbase output with a spendable one. Fix at the source rather than relying on callers to implement the `txdata[1 ..]` post-processing documented in the comment.

### Proof of Concept
```rust
// Conceptual: a miner-crafted block whose coinbase pays Serai's P2TR script.
let mut coinbase = Transaction { /* valid coinbase */ .. };
coinbase.output.push(TxOut {
  value: Amount::from_sat(50_000),
  script_pubkey: serai_p2tr_script.clone(), // script registered in Scanner::scripts
});
let block = Block { header: h, txdata: vec![coinbase, /* other txs */] };

// scanner.scan_block(&block) returns a ReceivedOutput for the coinbase output,
// indistinguishable from a spendable output. A downstream plan spending
// outpoint { txid: coinbase.compute_txid(), vout } is consensus-invalid for
// 100 blocks (BIP-30/COINBASE_MATURITY), yet the output was reported received.
```
Relevant code: `scan_block` iterates all of `block.txdata` including index 0 and delegates to `scan_transaction`, which emits `ReceivedOutput` purely on `script_pubkey` match with no maturity/tx-position check (networks/bitcoin/src/wallet/mod.rs:199-227). The doc comment at lines 218-220 acknowledges the issue but leaves the unsafe default in place.