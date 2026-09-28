### Title
Deposits below the dust threshold to Serai Bitcoin addresses are permanently unrecoverable - (File: processor/src/networks/bitcoin.rs)

### Summary
Analogous to the Reservoir report (funds sent to the contract become stuck with no recovery path), any Bitcoin output paying to a Serai-controlled P2TR script whose value is below `Bitcoin::DUST` (10,000 sats) is silently discarded by the processor's output scanner. The group key can cryptographically spend such outputs, but the processor never records them and exposes no recovery path, so they are permanently stranded.

### Finding Description
`Bitcoin::get_outputs` scans every non-coinbase transaction in a block and, for each matched output, only retains it if `output.balance().amount.0 >= N::DUST`; smaller outputs are dropped without being emitted as `ScannerEvent::Block` outputs, stored in `ram_outputs`, or written to the scanner DB (`processor/src/networks/bitcoin.rs` lines 686-700, filter at line 564 in `processor/src/multisigs/scanner.rs`).

`DUST` is set to `10_000` sats — deliberately ~35× the Bitcoin Core dust relay limit (commented reasoning at `processor/src/networks/bitcoin.rs:606-638`), justified as "ensure this is actually worth our time." This is an economic convenience threshold, not a consensus or spendability limit: a 9,999-sat P2TR output to `key + offset*G` is fully spendable by the FROST multisig.

The same applies to every publicly derivable Serai address — `external_address`, `branch_address`, `change_address`, and `forward_address` all use deterministic `hash_to_F`-derived offsets registered in `scanner()` (`processor/src/networks/bitcoin.rs:314-346`), so an unprivileged depositor can compute and pay any of them. The `Scanner` itself (`networks/bitcoin/src/wallet/mod.rs:199-214`) *does* match and return such outputs (the drop happens only in the processor layer), confirming the output is recognized and spendable, then intentionally orphaned.

Additionally, `SignableTransaction::new` only ever consumes `ReceivedOutput`s the scheduler hands it, and there is no administrative/manual recovery path for outputs the scanner skipped — mirroring the Reservoir finding exactly: the contract/wallet holds spendable funds it will never move.

### Impact Explanation
Permanent loss of user/protocol funds. A depositor who sends any amount below 10,000 sats to their Serai deposit/forward address loses those coins forever even though the validator set controls the key. At scale this also applies to change-fragment edge cases and to any future raise of real relay dust limits. Loss requires no attacker capability beyond sending a normal Bitcoin transaction, though it does require the sender to choose a small amount — bounded, accidental fund loss.

### Likelihood Explanation
Low-to-moderate: it requires a depositor to send less than 10,000 sats (~dust-sized) to a Serai address. This is a plausible mistake (wallet dust, test deposits, fee-sensitive wallets consolidating tiny UTXOs), and unlike the Reservoir case the Serai architecture actively uses these addresses for arbitrary external senders, not a single controlled token.

### Recommendation
Lower `Bitcoin::DUST` to the true economic floor (a small multiple of the spend fee, e.g. the documented ~285 sat cost-to-spend) rather than 10,000, or emit sub-dust outputs in `ScannerEvent::Block` flagged as unspendable so the scheduler/coordinator can aggregate them later or refund them via `presumed_origin`. At minimum, index dropped outputs in the DB so a manual recovery signing session can be constructed.

### Proof of Concept
1. Complete DKG for a Bitcoin `ThresholdKeys` set; compute `Bitcoin::external_address(key)` via `address_from_key(key)` (`processor/src/networks/bitcoin.rs:578-583, 657-659`).
2. From any external wallet, broadcast a transaction paying 5,000 sats to that P2TR address and mine/confirm it.
3. The processor's `Scanner::run` calls `get_outputs`; `scanner.scan_transaction(tx)` returns the `ReceivedOutput` (it matches the registered script_pubkey), but `output.balance().amount.0 = 5000 < DUST = 10000` so it is dropped at `processor/src/multisigs/scanner.rs:564`.
4. Result: the output is on-chain and spendable by `group_key` (offset `Scalar::ZERO`, even-Y enforced by `tweak_keys`/`p2tr_script_buf`), but it never enters `ram_outputs`, the scanner DB, or any `SignableTransaction` input set — indistinguishable from never having been received, with no code path able to reclaim it.