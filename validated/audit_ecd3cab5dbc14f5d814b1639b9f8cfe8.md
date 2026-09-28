### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class in the external report is a missing post-condition/invariant validation around an operation whose result can be degraded by an unprivileged counterparty. In `bitcoin-serai`, the same shape exists in the UTXO-scanning path: `Scanner::scan_block` iterates over every transaction in a block, including `block.txdata[0]` (the coinbase), and `Scanner::scan_transaction` matches purely on `script_pubkey` with no maturity or spendability validation. Any miner — an unprivileged third party — can direct a coinbase output to a scanner-registered Taproot `script_pubkey`. The library then emits a `ReceivedOutput` that downstream code treats as an immediately spendable input, even though consensus rules forbid spending it for 100 blocks.

### Finding Description
`Scanner::new` builds the script map and `register_offset` maps additional offsets to P2TR `script_pubkey`s at `networks/bitcoin/src/wallet/mod.rs:162-196`. `scan_transaction` matches `output.script_pubkey` against that map and produces `ReceivedOutput { offset, output, outpoint }` with no other checks (`networks/bitcoin/src/wallet/mod.rs:199-214`). `scan_block` then scans `block.txdata` wholesale, explicitly including the coinbase (`networks/bitcoin/src/wallet/mod.rs:221-227`), while the doc comment at lines 216-220 concedes coinbase outputs "are bound by maturity" and pushes the filtering burden onto the caller.

These `ReceivedOutput`s feed directly into `SignableTransaction::new`, which performs dust/fee/balance checks but no maturity check (`networks/bitcoin/src/wallet/send.rs:150-256`), and into `TransactionSignMachine::sign`, which produces a real threshold signature over a transaction spending the immature outpoint (`networks/bitcoin/src/wallet/send.rs:355-398`). The result is a fully signed transaction spending a coinbase output that is consensus-invalid for ~100 blocks. Notably, the higher-level processor-side scanner compensates for this by skipping `txdata[0]` (`processor/src/networks/bitcoin.rs:691`), confirming the hazard is real and that the fix belongs in the in-scope wallet code which lacks it.

### Impact Explanation
An external party (any miner or mining pool) can cause a Serai wallet/scanner instance to report funds as received that are not spendable, and can cause the multisig to produce signed transactions that cannot be mined until maturity. Where outputs drive downstream actions (forwarding plans, crediting deposits, liquidity accounting), counting a 100-block-immature coinbase as immediately usable liquidity is the direct analog of the report's "vault left in a degraded state that validation should have rejected." The signature share expenditure on an invalid transaction also creates an availability impact: the plan consumes the inputs and coordinator signing rounds on a transaction that will be rejected by the mempool.

### Likelihood Explanation
The attack requires mining a block (or influencing a pool's coinbase construction), which has real cost, and a coinbase paying a non-self script is unusual. However, `scan_block` is a public API intended for block-level scanning, the code path requires no special access beyond being the block's miner, and nothing in `scan_transaction`/`scan_block`/`SignableTransaction::new` rejects or delays the output. The library's own doc comment acknowledges the gap, and the processor compensates for it elsewhere — indicating the validation is absent where the invariant should be enforced. Severity is Medium: impact is bounded (funds are eventually spendable, not stolen), but the "reported received, actually unspendable" invariant violation is reachable from public transaction data.

### Recommendation
Enforce the maturity invariant inside the in-scope wallet code rather than relying on callers:

- In `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`), skip `block.txdata[0]`, or tag coinbase-derived `ReceivedOutput`s with a maturity height so they are only emitted/spendable after 100 confirmations.
- Add a corresponding check in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`) so an immature outpoint passed as an input produces a `TransactionError` instead of a signed invalid transaction — the post-condition check the bug class demands.

### Proof of Concept
```rust
// Conceptual: attacker is a miner who knows the vault's external script_pubkey.
// They set their coinbase reward output to script_pubkey = p2tr_script_buf(key).

let coinbase = Transaction {
  // txdata[0]: coinbase paying COINBASE_MATURITY-locked funds to the scanned script
  output: vec![TxOut { value: Amount::from_sat(50_0000_0000), script_pubkey: scanned_script }],
  ..Default::default()
};
let block = Block { txdata: vec![coinbase], ..Default::default() };

// Scanner::scan_block iterates txdata[0] and reports it as a ReceivedOutput
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1); // reported as received, spendable per the API

// SignableTransaction::new accepts it; TransactionSignMachine::sign produces a
// fully signed TX spending an immature coinbase outpoint -> consensus-invalid
// for 100 blocks. Funds were "received" per the scanner yet cannot be spent.
```
The concrete contrast is `processor/src/networks/bitcoin.rs:691`, which slices `&block.txdata[1 ..]` to avoid exactly this, while the in-scope `Scanner::scan_block` does not.