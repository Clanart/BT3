### Title
Unprivileged sender permanently halts `Bitcoin::get_outputs` via infinite `getrawtransaction` retry, freezing all multisig funds - (File: processor/src/networks/bitcoin.rs)

### Summary
The Astaria bug class is: an unprivileged party injects a minimal input (a 1-wei deposit into `WithdrawProxy`) that corrupts shared accounting such that the fund-processing routine (`processEpoch`) permanently reverts, and no one can withdraw. The Serai analog lives in the Bitcoin network scanner. `Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:686-740`) is invoked for every registered multisig key on every scanned block (`processor/src/multisigs/scanner.rs:562`). For any transaction that pays to a scanned script, it unconditionally resolves `tx.input[0]`'s parent via `rpc.get_transaction` inside an unbounded `while tx.is_err() { sleep(5s) }` retry loop (`processor/src/networks/bitcoin.rs:714-727`). `get_transaction` calls `getrawtransaction` **without** a block-hash argument (`networks/bitcoin/src/rpc.rs:211-225`), which on nodes without `txindex=1` (the default) fails for any confirmed transaction — including the attacker's own parent. The loop never exits, `Scanner::run` (`processor/src/multisigs/scanner.rs:438-`) stalls permanently at that block, and no later block, deposit, eventuality completion, or payment is ever processed. Like `processEpoch` reverting forever, the vault's BTC accounting is frozen by a single on-chain input an unprivileged party created.

### Finding Description
1. `Scanner::new`/`register_offset` build a `scripts` map keyed solely by `script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:153-196`). Anyone can learn the multisig's external P2TR address and pay to it — the scanner will match the output.
2. For any tx containing a matched output, `get_outputs` reads `tx.input[0]`, derives `spent_tx = previous_output.txid`, and loops `getrawtransaction` forever until success, logging and sleeping 5s on every error (`processor/src/networks/bitcoin.rs:714-725`). There is no timeout, no fallback, and no way to skip the offending output.
3. `Rpc::new` only verifies that the `getrawtransaction` *method* exists (`networks/bitcoin/src/rpc.rs:75-83`); it does not verify `txindex`. Without txindex, `getrawtransaction(txid)` errors for every confirmed, non-mempool transaction — so the parent's fetch fails deterministically and forever.
4. `Scanner::run` calls `get_outputs` while holding the scanner write-lock (`processor/src/multisigs/scanner.rs:544-567`); the hang blocks all subsequent block scanning, `ack_block`, and downstream scheduling/signing for the multisig — a total liveness failure of deposits and withdrawals, reachable by any party who sends Bitcoin to the public multisig address.
5. Secondary unprivileged-triggerable faults in the same function: `tx.input[0]` indexes without a length check (consensus currently guarantees ≥1 input for non-coinbase txs, so low risk), and `usize::try_from(input.previous_output.vout).unwrap()` (`processor/src/networks/bitcoin.rs:726`) unwraps attacker-influenced data.

### Impact Explanation
Total and permanent denial of service of the affected multisig's Bitcoin operations: once the malicious (or even ordinary, under a no-txindex node) transaction enters a block being scanned, the scanner's `run` loop is stuck in the retry `while` forever. No outputs are reported, no payments are scheduled, no forwarded/refund/retirement transactions occur. All BTC held by the multisig becomes unreachable, directly paralleling the Astaria report's "no one will be able to receive their funds" outcome. Rated High: loss of liveness/funds-freeze triggered by a transaction any Bitcoin user can send for the cost of one output.

### Likelihood Explanation
The external multisig addresses are public; sending an output to them costs only a UTXO's worth of BTC. The only mitigation is that the loop succeeds if the connected node runs `txindex=1` (all confirmed parents retrievable) — but nothing in `Rpc::new` or `get_outputs` enforces or even checks that deployment assumption, and the failure mode is silent and unrecoverable without code changes and rescanning. The panic paths (`input[0]` on a hypothetical empty-input tx — not consensus-valid today — and the `vout` unwrap) are weaker but share the same root cause: unbounded trust in attacker-supplied transaction topology.

### Recommendation
- Bound the retry loop: on repeated `getrawtransaction` failure, fall back to `getblock`-derived parent resolution or record-and-skip the output with an alert, never blocking the scan loop indefinitely.
- Have `Rpc::new` verify `txindex` (e.g., a probe `getrawtransaction` on a known confirmed txid, or the `getindexinfo` method) so the misconfiguration is caught at startup rather than at scan time.
- Replace `tx.input[0]` with `tx.input.first().ok_or(...)` and the `unwrap()` on `vout` conversion with checked errors.

### Proof of Concept
Conceptual (requires a Bitcoin node + regtest or mainnet):

```rust
// Attacker, knowing the multisig external address A (a P2TR script_pubkey
// registered in Scanner.scripts, networks/bitcoin/src/wallet/mod.rs:164):
// 1. Create TX1 paying themselves; TX1 confirms.
// 2. Create TX2 spending TX1's output, paying >= DUST to A. TX2 confirms.
//
// Serai processor connected to a node WITHOUT txindex:
//   Scanner::run -> get_outputs(block, key)
//     -> scan_transaction matches TX2's output to A
//     -> input = &TX2.input[0]                       // processor/src/networks/bitcoin.rs:715
//     -> rpc.get_transaction(TX1.txid)               // getrawtransaction w/o blockhash
//        -> RpcError (no txindex, TX1 not in mempool)
//     -> `while tx.is_err() { sleep(5s) }`           // bitcoin.rs:719-725, never exits
//
// Result: Scanner::run stuck at this block; all subsequent deposits,
// eventuality completions, and payments for this multisig are never
// processed — a permanent freeze of the vault's Bitcoin, caused by a
// single unprivileged on-chain transaction.
```

Caveat: the deterministic hang requires the RPC node to lack txindex (or to prune), which the code never validates; with `txindex=1` the parent fetch always succeeds and this path reduces to a robustness defect rather than a live exploit. Under the prompt's severity rules this is a reachable High DoS conditional on a deployment configuration the code fails to enforce or document.