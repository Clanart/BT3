### Title
`Scanner::scan_block` reports immature coinbase outputs as received, causing funds to be accepted that cannot be spent - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to GPToke marking rewards as claimed before the fallible transfer completes, `Scanner::scan_block` marks an output as received (and indistinguishable from any other `ReceivedOutput`) before the precondition for spending it — coinbase maturity — is satisfied. An output is credited to the wallet/multisig while the protocol cannot actually spend it.

### Finding Description
`scan_transaction` matches only `output.script_pubkey` against the registered scripts and builds a `ReceivedOutput` carrying just `offset`, `output`, and `outpoint` — no flag or height indicating coinbase status (mod.rs:199-214). `scan_block` iterates `block.txdata` starting at index 0, so the coinbase transaction is scanned identically to normal transactions (mod.rs:221-227). The code comment itself acknowledges the hole: "This will also scan the coinbase transaction which is bound by maturity… a post-processing pass is needed," yet the API does not perform that pass or expose the data needed to do it (`ReceivedOutput` stores no `is_coinbase` marker).

Bitcoin consensus rules forbid spending a coinbase output until it has 100 confirmations. A miner (or anyone who learns a multisig's registered offset script, e.g., a tweak derived from a public `InInstruction`) can pay to the Serai multisig script in a coinbase transaction. `scan_block` reports it as a normal received output.

### Impact Explanation
The scanner's output is the source of truth for "funds received." An immature coinbase output reported as received can be:
- Credited to a depositor whose coins are not yet spendable, and if spent upstream of maturity, the entire `SignableTransaction` built from `prevouts` (send.rs:175-185) is consensus-invalid, stalling the `Batch`.
- Selected as an input in `SignableTransaction::new`, producing a transaction the FROST signing flow signs correctly but that the network rejects at `publish_completion`, wasting a signing attempt and potentially burning fee budget earmarked in `needed_fee`.

This maps directly to the report's class: value is recorded before the fallible precondition (maturity, not a transfer call) is verified, and the recorded state does not capture why the spend failed.

### Likelihood Explanation
Low to medium. It requires a miner willing to direct coinbase payout to the multisig script (or a third party funding the coinbase's outputs — coinbase `tx.output` can pay arbitrary scripts). No privileged access is needed; any party can craft the on-chain data fed to `scan_block`. Severity is bounded by the fact that the output becomes spendable after 100 confirmations, so it is a liveness/accounting-integrity issue rather than permanent theft — hence Medium.

### Recommendation
In `scan_transaction`/`scan_block`, skip or flag coinbase outputs: check `tx.is_coinbase()` in `scan_block` and either exclude `block.txdata[0]` or tag the resulting `ReceivedOutput` with the block height/coinbase flag so downstream callers can enforce the 100-block maturity rule before treating it as spendable. Alternatively, change the API to return `(ReceivedOutput, is_coinbase)` pairs.

### Proof of Concept
```rust
// conceptual: a block whose coinbase pays to a registered offset script
let block: Block = ...; // block.txdata[0] is a coinbase paying `scanner_script`
let outputs = scanner.scan_block(&block);
// outputs contains a ReceivedOutput for the coinbase vout, indistinguishable
// from a mature payment. SignableTransaction::new will accept it as an input,
// and the resulting TX fails mempool acceptance (bad-txns-premature-spend-of-coinbase).
```
Supporting code: `scan_block` iterates all of `block.txdata` including index 0 (mod.rs:221-227), and `scan_transaction` records any output whose `script_pubkey` matches (mod.rs:201-211) with no maturity check.