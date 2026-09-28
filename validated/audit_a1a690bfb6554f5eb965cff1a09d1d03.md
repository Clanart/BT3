### Title
Sub-dust Bitcoin outputs received at multisig addresses are silently dropped by the processor's Scanner, so deposits below `DUST` are received on-chain but never credited or spendable - ([File: processor/src/multisigs/scanner.rs](processor/src/multisigs/scanner.rs))

### Summary
The external report describes a rounding/class mismatch: a positive external transfer can collapse to a zero internal accounting update, so the protocol records value on one side of the ledger with no corresponding entry on the other. The analog in Serai lives in the processor's Bitcoin scanning path: `Scanner::run` filters `network.get_outputs(&block, key)` results by `output.balance().amount.0 >= N::DUST` (`processor/src/multisigs/scanner.rs`, lines 562-566), where `Bitcoin::DUST = 10_000` sats (`processor/src/networks/bitcoin.rs:638`). `bitcoin_serai`'s `Scanner::scan_transaction` itself returns *every* output paying to a registered script regardless of value (`networks/bitcoin/src/wallet/mod.rs:199-214`), so the funds genuinely arrive at a Serai-controlled Taproot output, but the processor silently discards them at the `DUST` gate.

### Finding Description
The core inconsistency mirrors the Notional issue: the "underlying" side (the actual Bitcoin UTXO, spendable by the multisig key + offset) increases, while the "supply" side (the outputs passed into the scheduler/`ScannerEvent::Block` event, which drives crediting) records nothing. Specifically:

- `Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` pushes any `TxOut` whose `script_pubkey` matches a registered script into `Vec<ReceivedOutput>` with no minimum value check.
- In `processor/src/multisigs/scanner.rs` (`for output in network.get_outputs(&block, key).await { ... if output.balance().amount.0 >= N::DUST { outputs.push(output); } }`), outputs below 10,000 sats are dropped before being persisted via `save_outputs` or emitted in `ScannerEvent::Block`.
- Because the drop happens *before* `seen_key` marking (`save_scanned_block` marks only saved outputs as seen), nothing prevents reconsideration — but also nothing ever credits it, and there is no error path, event, or log for the dropped output (the `log` only fires for outputs that pass the filter).
- The multisig controls these funds (the key/offset pair in the `ReceivedOutput` is valid and spendable via `SignableTransaction`), but no code path will ever aggregate them, since the scheduler only consumes outputs the scanner emitted. They are permanently stranded, exactly like Notional's underlying which accrues with `netPrimeSupplyChange == 0`.

Note the asymmetry with spending: `SignableTransaction::new` refuses to *create* dust outputs (`DustPayment` for payments, silent drop-to-fee for change at `send.rs:165-169, 224-235`), which is correct accounting — the discrepancy only exists on the receive path.

### Impact Explanation
An unprivileged depositor (or any third party) can send a transaction paying e.g. 546–9,999 sats to a Serai deposit/branch/change script. The funds land on-chain under the multisig's control, yet the processor never emits them, so no credit is minted and the scheduler never attempts to aggregate the output. The BTC is effectively burned: recoverable only by direct out-of-band signing with the correct offset, which the normal protocol flow will never perform. This is the accepted "funds received that are not creditable/spendable" impact class, and it also creates a small imbalance where the multisig's true on-chain balance exceeds all accounted balances — value accrues to remaining participants unattributably, matching the original finding's "some users can claim extra underlying" shape.

### Likelihood Explanation
Any user who miscalculates fees, sweeps a small UTXO, or deliberately griefs by dusting a known Serai deposit address triggers this with a single ordinary Bitcoin transaction. No validator misbehavior or timing is required; the filtering code runs deterministically on every scanned block.

### Recommendation
Either reject sub-`DUST` outputs explicitly (emit a distinct scanner event / log and surface them as unspendable-but-received so the accounting layer can record them), or aggregate dust inputs opportunistically when they can ride along with other inputs without making the transaction non-standard — i.e., ensure every received output produces a nonzero accounting entry, mirroring the recommendation that `netPrimeSupplyChange != 0` whenever `netTransferUnderlyingExternal > 0`.

### Proof of Concept
```rust
// Conceptual, against networks/bitcoin + processor scanner
// 1. Derive the multisig's P2TR script:
let script = p2tr_script_buf(key).unwrap();
// 2. Send a real Bitcoin tx paying `script` with value = 500 sats (< DUST = 10_000).
// 3. `Scanner::scan_transaction(&tx)` returns the ReceivedOutput (no value filter).
// 4. Processor `Scanner::run`: `output.balance().amount.0 >= N::DUST` is false;
//    the output is dropped. No `ScannerEvent::Block` output, no `seen` marking,
//    no log — the deposit is never credited and never spent.
```