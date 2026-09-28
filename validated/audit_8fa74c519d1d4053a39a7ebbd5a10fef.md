### Title
Scanner reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in a block, including the coinbase transaction (`block.txdata[0]`), and returns matching outputs as `ReceivedOutput` — a type documented as "A spendable output" — without any check that the output is actually mature enough to spend.

### Finding Description
`scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) matches outputs purely on `script_pubkey` against registered offset scripts and wraps each match in a `ReceivedOutput` with no contextual validation. `scan_block` (mod.rs:221-227) iterates over the entire `block.txdata`, so a coinbase output paying to a registered P2TR script is returned identically to a normal confirmed output. This mirrors the reported bug class: data pulled from an external source is accepted with only a superficial validity check (script match, analogous to `answer < 0`) while the semantically critical freshness/maturity property is unchecked. The only guard is a doc comment on `scan_block` stating "If received outputs must be immediately spendable, a post-processing pass is needed" — the API itself neither enforces maturity nor exposes the coinbase-ness of an output on `ReceivedOutput`, so a downstream caller has no way to distinguish the tainted outputs from the return value alone.

### Impact Explanation
Coinbase outputs are unspendable for 100 blocks per Bitcoin consensus rules. A `ReceivedOutput` produced from a coinbase transaction is therefore "funds reported received that are not spendable": any code treating the result as spendable will construct transactions that fail relay/consensus, and any accounting treating them as available balance overstates liquid funds. If a reorg orphans the block, the reported output never existed at all.

### Likelihood Explanation
Any miner (an unprivileged party relative to the multisig) can pay the tweaked group key in a coinbase transaction; scanning is performed on public block data. P2TR coinbase outputs are unusual but fully legal, so the trigger requires only that a miner chooses to do it, or that such an output occurs incidentally.

### Recommendation
Either skip `block.txdata[0]` in `scan_block` (or accept a flag), or record maturity status on `ReceivedOutput` (e.g., an `is_coinbase`/maturity field written by `write`/`read`) so callers cannot silently treat immature outputs as spendable.

### Proof of Concept
```rust
// networks/bitcoin context
let mut scanner = Scanner::new(group_key).unwrap();
let offset = scanner.register_offset(some_offset).unwrap();
// Miner builds a block whose coinbase pays to the offset's P2TR script
let block: Block = /* block with coinbase output to scanner script */;
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput claiming to be "a spendable output",
// yet spending it within 100 blocks violates consensus (BIP-30/BIP-34 maturity rule);
// there is no field on ReceivedOutput distinguishing it from a normal output.
```
Relevant code: `ReceivedOutput` (mod.rs:88-118), `scan_transaction` (mod.rs:199-214), `scan_block` (mod.rs:216-227).