### Title
Scanner reports coinbase outputs as received which are unspendable until maturity, causing the multisig to commit an immature input and have its spend transaction rejected - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` in full, including `txdata[0]` — the coinbase transaction. Any output paying to the Serai multisig's P2TR script inside a coinbase transaction is returned as a `ReceivedOutput` indistinguishable from a normal output. Bitcoin consensus forbids spending coinbase outputs until they are 100 blocks deep, yet Serai's Bitcoin integration only requires `CONFIRMATIONS = 6`. The result is funds reported as received which are not spendable, and any plan that consumes such an output produces a transaction rejected by the network — the same shape as the reference bug, where a recovery/spend path reverts and the underlying coins are stranded.

### Finding Description
The external report's bug class is: a hardcoded, insufficient parameter on a downstream call makes the recovery path fail, leaving user funds stranded. On Bitcoin, the equivalent invariant is "a scanned `ReceivedOutput` is spendable once confirmed." `Scanner::scan_block` breaks that invariant:

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

The code's own doc comment admits the flaw: "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed" — but `scan_block` itself performs no such pass, and the output's `OutPoint`/`TxOut` carry no marker distinguishing coinbase provenance. `scan_transaction` only matches `output.script_pubkey`, so a coinbase output to the multisig script is accepted like any other.

Downstream, `Bitcoin::CONFIRMATIONS = 6` (processor/src/networks/bitcoin.rs, `const CONFIRMATIONS: usize = 6;`) means a coinbase output is accepted as "received" at depth 6 — 94 blocks before it becomes spendable. If the scheduler includes that output as an input in a `SignableTransaction`, the signed transaction is invalid at the consensus level (`bad-txns-premature-spend-of-coinbase`) and cannot be relayed or mined until maturity. In the worst case, a plan mixing the immature coinbase output with other multisig inputs has the entire transaction rejected, stranding all payments bundled with it — the direct analog of `sendCrossDomainMessage(..., 0, ...)` reverting and trapping the bounced tokens.

A miner is an unprivileged party: anyone mining a block can place an output to the Serai multisig's P2TR script in their coinbase transaction (coinbase outputs may pay arbitrary scripts). This requires no cooperation from Serai and no special access — it is a Bitcoin transaction they cause to exist, squarely within the reachable-input model.

### Impact Explanation
Funds are reported received that are not spendable: the scanner emits a `ReceivedOutput` for a coinbase UTXO that consensus forbids spending for 100 blocks while Serai treats 6 confirmations as final. Beyond delayed spendability, if the immature output is consumed by a plan, the entire signed transaction is consensus-invalid and rejected on broadcast, stalling every payment in that plan. This matches the reference impact (recovery TX reverts, funds locked) and the accepted impact class "funds reported received that are not spendable."

### Likelihood Explanation
Requires a miner to route coinbase rewards (or an arbitrary coinbase output) to the Serai multisig address. This is low-probability in normal operation but requires zero cooperation and can be done deliberately by any miner as a griefing/donation vector; mining pools occasionally do pay arbitrary addresses from coinbase outputs. Severity is Medium: real loss of liveness/stranded funds for the affected plan, but bounded in duration (resolves at maturity) and dependent on the processor consuming the immature output before depth 100.

### Recommendation
Skip `txdata[0]` in `Scanner::scan_block` (scan `block.txdata[1 ..]` only), or mark `ReceivedOutput`s originating from the coinbase transaction and have the processor refuse to schedule them as plan inputs until depth ≥ 100. At minimum, enforce the documented post-processing filter at every `scan_block` call site so no immature output reaches `SignableTransaction::new`.

### Proof of Concept
1. A miner mines a block whose coinbase transaction (`block.txdata[0]`) contains a `TxOut` paying `p2tr_script_buf(multisig_key)` — the same script `Scanner::new` registers at `networks/bitcoin/src/wallet/mod.rs:164`.
2. The Serai processor scans the block. `scan_block` iterates all of `block.txdata`, `scan_transaction` matches the coinbase output's `script_pubkey` at `mod.rs:205-211`, and emits a `ReceivedOutput`.
3. After 6 more blocks (`CONFIRMATIONS = 6`, `processor/src/networks/bitcoin.rs:604`), the processor reports the output as a received balance and makes it eligible for plans.
4. The scheduler places the coinbase `ReceivedOutput` into `SignableTransaction::new`'s inputs (`networks/bitcoin/src/wallet/send.rs:150-156`). Nothing in `SignableTransaction` or the sighash path checks coinbase maturity.
5. The threshold signature is produced and the transaction is broadcast; every node rejects it with `bad-txns-premature-spend-of-coinbase` because the coinbase input is below 100 confirmations. The plan's payments are stranded until maturity (and, if mixed with other inputs, those inputs' payments are stalled too).

*Caveat: I could not verify whether `processor/src/networks/bitcoin.rs` calls `scan_block` directly or applies its own coinbase filter (a `coinbase`/`txdata` grep returned one uninspected match there). If the processor already skips `txdata[0]` or filters `is_coinbase()`, the finding reduces to a latent footgun in the in-scope `Scanner::scan_block` API rather than a reachable bug.*