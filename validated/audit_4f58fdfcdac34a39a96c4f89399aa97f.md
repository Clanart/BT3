### Title
`Scanner::scan_block` credits coinbase outputs as spendable received funds despite the 100-block maturity rule - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata`, which includes the coinbase transaction at index 0, and feeds it through the same `scan_transaction` path used for normal transactions. Any output of a coinbase transaction paying to a registered Serai script is returned as a `ReceivedOutput` indistinguishable from a regular, immediately spendable output.

### Finding Description
The external report's bug class is "a deposit/credit is recorded even though the asset was not actually delivered under the assumed semantics" — in Teller, a collateral deposit was recorded even though the token transfer silently failed. The analogous shape in Serai is in `Scanner`:

- `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`) maps any output whose `script_pubkey` matches a registered script to a `ReceivedOutput`, exposing `value()`, `output()`, `outpoint()`, and `offset()` — i.e., everything needed to treat it as owned, spendable funds.
- `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`) iterates `for tx in &block.txdata`, including `block.txdata[0]`, the coinbase.

Bitcoin consensus forbids spending coinbase outputs until they are 100 blocks deep. A `ReceivedOutput` derived from a coinbase therefore describes funds that are *reported received* but are *not spendable*: the `outpoint` cannot be referenced as an input, and any signing flow built on it produces a transaction the network rejects. A caller that simply iterates `scan_block` results — the natural API for "what did we receive in this block" — has no way, from the `ReceivedOutput` structure itself, to tell a mature output from an immature coinbase output. `ReceivedOutput` carries no flag distinguishing its provenance, and `scan_block` applies no filter.

This is the same root cause as the Teller report: a single code path handles two asset classes with different validity semantics (`ERC721` vs `ERC20`; coinbase vs regular output), and the path assumes the semantics of one while silently accepting the other, crediting value that cannot actually be used.

### Impact Explanation
Funds are reported received that are not spendable. Any downstream accounting or signing pipeline that consumes `scan_block` output will credit immature coinbase outputs and may construct transactions referencing an `OutPoint` that consensus rejects, or report a balance that cannot be backed by signatures. This matches the accepted impact class "funds reported received that are not spendable."

### Likelihood Explanation
An unprivileged party reaches this with public inputs: `scan_block` accepts any `&Block`. Miners routinely produce coinbase transactions, and any miner (or anyone whose transaction ends up adjacent, though coinbase outputs specifically require mining) can pay to a watched Serai script. More directly, any valid block fed to `scan_block` containing a coinbase payment to a registered script produces the mis-credited `ReceivedOutput`. While the doc comment notes a post-processing pass is needed, the API still emits these outputs unconditionally and `ReceivedOutput` gives the consumer no marker to perform that filtering correctly (the coinbase-ness is not encoded; the caller must independently re-derive `tx.compute_txid()` membership in `txdata[0]`).

### Recommendation
In `scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`), skip `block.txdata[0]` (the coinbase), or return coinbase-derived `ReceivedOutput`s through a distinct type/flag so callers cannot conflate them with spendable outputs. At minimum, iterate `block.txdata[1 ..]` and document that coinbase outputs must be scanned separately once mature.

### Proof of Concept
Conceptual, against `networks/bitcoin/src/wallet/mod.rs`:

```rust
// Given a Scanner with a registered script S (e.g., Scanner::new(key)),
// construct or obtain a Block whose coinbase transaction (txdata[0])
// contains an output { value: V, script_pubkey: S }.
let outputs = scanner.scan_block(&block);
// outputs contains a ReceivedOutput for the coinbase output.
// output.value() == V, and outpoint points at the coinbase txid:vout.
// Any attempt to spend this outpoint is rejected by consensus for 100 blocks,
// yet it is indistinguishable from a mature payment in the returned Vec.
```

No txid/provenance check exists in `scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`) and `scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`) iterates all of `block.txdata` including index 0.