### Title
`Scanner::scan_block` reports coinbase (immature) outputs as spendable received funds — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Node.js advisory describes a server that keeps accepting and processing data on a stream after having sent a terminating `GOAWAY` frame — input is accepted past a protocol-defined point of finality. The closest Serai analog lives in the Bitcoin wallet `Scanner`: `scan_block` iterates over **every** transaction in `block.txdata`, including `block.txdata[0]`, the coinbase transaction. A coinbase output is bound by Bitcoin's 100-block maturity rule — it exists on-chain yet is consensus-invalid to spend until 100 further blocks. The scanner carries on accepting outputs from a transaction the protocol itself declares not-yet-spendable, returning them as ordinary `ReceivedOutput`s indistinguishable from mature funds.

### Finding Description
`Scanner::scan_block` (networks/bitcoin/src/wallet/mod.rs:221-227) loops `for tx in &block.txdata` and calls `scan_transaction` on each. `scan_transaction` (mod.rs:199-214) matches `output.script_pubkey` against registered scripts and produces a `ReceivedOutput { offset, output, outpoint }` with no maturity annotation — the `ReceivedOutput` type (mod.rs:88-97) has no field recording coinbase status, so maturity information is irrecoverably lost at the scan boundary. The only mitigation is a doc comment telling integrators they "may" post-process or call `scan_transaction` on `block.txdata[1 ..]` instead. Any miner can produce a coinbase transaction paying to a registered Serai P2TR script (including a multisig address or an offset-derived script registered via `register_offset`); mining is permissionless. The scanner will then emit that UTXO as a normally-received output.

### Impact Explanation
Impact: **funds reported received that are not spendable**, an accepted impact class. Downstream, an output emitted by the scanner is treated as owned, spendable balance: it can be scheduled into a `Plan` and fed into the FROST signing pipeline to construct a spend transaction. A transaction spending a coinbase output younger than 100 blocks fails consensus validation (`bad-txns-premature-spend-of-coinbase`) — the signed transaction cannot be published, stalling the plan/batch machinery, or, if the output was reported to Serai as received liquidity backing minted sriBTC, the ledger records solvent-backing funds that cannot actually move for ~100 blocks. Additionally, coinbase outputs disappear entirely on a reorg (rather than reverting to mempool), so a scanned "received" output can simply cease to exist.

### Likelihood Explanation
Likelihood is bounded but real: triggering requires mining a Bitcoin block whose coinbase pays a Serai-watched script — feasible for any miner or mining-pool participant (public input, no validator privilege needed), and incidental payouts happen in practice. The trigger probability is low; the documentation warning reduces but does not eliminate exposure, since `scan_block` is the natural block-level entry point and discards the maturity flag rather than propagating it (making downstream correction impossible after the fact). Assessment: Medium.

### Recommendation
Skip `block.txdata[0]` when it is a coinbase (`tx.is_coinbase()`), or better, thread a maturity marker through `ReceivedOutput` (e.g., a `coinbase: bool` or a `mature_at` height) so callers can defer the output until maturity rather than losing the information at the deserialization/scan boundary. At minimum, `scan_block` should not silently merge immature and mature outputs into one untyped `Vec`.

### Proof of Concept
```rust
// networks/bitcoin context — conceptual PoC
let mut scanner = Scanner::new(multisig_key_even_y).unwrap();
// A miner constructs a block whose coinbase pays the multisig P2TR script
let mut block = mined_block;
block.txdata[0].output.push(TxOut {
    value: Amount::from_sat(1_000_000),
    script_pubkey: p2tr_script_buf(multisig_key_even_y).unwrap(),
});
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput for outpoint (coinbase_txid, vout)
// Any spend tx built from it is consensus-invalid for 100 blocks,
// and vanishes entirely if the block is reorged out.
assert_eq!(outputs.len(), 1); // reported as received, yet unspendable
```

Caveat noted in analysis: the doc comment on `scan_block` (mod.rs:216-220) warns that coinbase outputs are maturity-bound and recommends post-processing, so this finding depends on judging that warning insufficient given the information loss in `ReceivedOutput`. Whether the production `get_outputs` path in the processor filters coinbase outputs could not be fully confirmed from the indexed snippet of `processor/src/networks/bitcoin.rs`; if it does filter, the reachable impact collapses to the library-level API hazard.