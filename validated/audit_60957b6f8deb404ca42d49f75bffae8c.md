### Title
`Scanner::scan_block` reports immature coinbase outputs as received spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` in full, including `txdata[0]` (the coinbase transaction). Coinbase outputs are bound by Bitcoin's 100-block maturity rule and cannot be spent until then, yet they are returned as ordinary `ReceivedOutput`s indistinguishable from spendable ones. A downstream consumer crediting the multisig balance on `scan_block` results will count funds that are not spendable, and any transaction built spending them is consensus-invalid.

### Finding Description
`scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches any output whose `script_pubkey` is registered, producing a `ReceivedOutput` with an offset and outpoint. `scan_block` (mod.rs:221-227) feeds every transaction in the block — including the coinbase — through it. There is no `tx.is_coinbase()` check, no maturity tracking, and `ReceivedOutput` carries no field indicating immaturity. A miner (an unprivileged party who constructs the coinbase transaction) can place a P2TR output paying to a registered multisig script in the coinbase of a block they mine. Any consumer scanning that block receives a `ReceivedOutput` that appears fully spendable but cannot be included in a transaction for 100 blocks (BIP-30/consensus coinbase maturity rule). The doc comment acknowledges this (`"This will also scan the coinbase transaction which is bound by maturity... a post-processing pass is needed"`), but the API provides no marker on `ReceivedOutput` to distinguish such outputs — the burden is entirely on the caller to know to skip `txdata[0]` or `scan_transaction` on it, while `ReceivedOutput::read`/`serialize` round-trips give no way to recover this distinction after the fact.

### Impact Explanation
Analogous to the report's "fees collected for positions never opened": value is recorded as legitimately received when the protocol cannot actually use it. A processor that credits incoming outputs to the multisig balance and/or schedules them as inputs to `Plan`s will (a) over-report received funds, and (b) build transactions referencing immature coinbase outpoints, which the Bitcoin network rejects as non-final/non-mature — burning signing rounds and potentially stalling payments. If such outputs are counted toward solvency/liability accounting, Serai reports solvency backed by coins it cannot move.

### Likelihood Explanation
Low-to-moderate: requires a miner to direct a coinbase output to a registered multisig script. This costs the miner nothing beyond constructing the output, and mining pools occasionally pay arbitrary scripts. The trigger is entirely under the unprivileged miner's control once a script is registered via `Scanner::new`/`register_offset`.

### Recommendation
Either skip the coinbase transaction inside `scan_block` (`for tx in &block.txdata[1..]` or `tx.is_coinbase()` check), or add an `is_coinbase`/maturity flag to `ReceivedOutput` so consumers can defer it. If coinbase outputs must remain scannable, require callers to pass block height and filter outputs until maturity.

### Proof of Concept
Conceptual: register `Scanner::new(key)`; a miner mines a block whose `txdata[0]` contains `TxOut { script_pubkey: p2tr_script_buf(key), value }`. `scan_block(&block)` returns `ReceivedOutput { offset: ZERO, outpoint: coinbase_outpoint }`. Any spend plan referencing that outpoint produces a transaction rejected by Bitcoin Core with `bad-txns-premature-spend-of-coinbase`. The only mitigation is external: calling `scan_transaction` per-`txdata[1..]`, which `scan_block` itself fails to do at mod.rs:223.