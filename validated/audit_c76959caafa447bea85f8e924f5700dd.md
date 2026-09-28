### Title
Deposits below the dust threshold are silently discarded and permanently locked at the multisig key - (File: processor/src/multisigs/scanner.rs)

### Summary
The external report describes assets sent to a contract that cannot be recovered because the contract lacks any withdrawal path for excess/rounding funds. The Serai analog exists in the Bitcoin deposit path: any on-chain output sent to a registered Serai multisig key whose value is below `N::DUST` (10,000 satoshis for Bitcoin) is silently dropped by the processor scanner. The funds remain spendable at the FROST group key in cryptographic terms, but the protocol has no mechanism to ever credit or spend them — they are permanently locked, an unrecoverable loss of assets reachable by any unprivileged depositor.

### Finding Description
When the processor scans a confirmed block, it enumerates outputs matching each registered key and filters them by the dust threshold before emitting them as received outputs:

- `processor/src/multisigs/scanner.rs:562-566` — `if output.balance().amount.0 >= N::DUST { outputs.push(output); }` — sub-dust outputs are silently skipped, never emitted via `ScannerEvent::Block`, and never recorded by `ScannerDb`.
- `processor/src/networks/bitcoin.rs:638` — `const DUST: u64 = 10_000;` — a fixed 10,000-sat threshold justified in comments as a 10x margin over relay-rule spendability (5000 sat/kvB × ~57 vB ≈ 285 sats).
- `networks/bitcoin/src/wallet/mod.rs:199-214` — `Scanner::scan_transaction` itself *does* return every output matching a registered `script_pubkey` regardless of value; it is the processor layer that discards sub-dust outputs after scanning.

Because the skipped output is never pushed into `outputs`, it is never acknowledged, never handed to the scheduler (`processor/src/multisigs/scheduler/utxo.rs`), and never usable as an input to `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`). There is no "sweep leftovers" or "credit small deposit" path anywhere in the signing/scheduler pipeline — the analog of the missing withdraw function in the external report.

A secondary, smaller instance of the same class exists in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:224-235`): when a change address is provided but the leftover is below `DUST` (or cannot cover `fee_with_change`), no change output is created and the remainder is silently burned as miner fee rather than returned — bounded by the dust amount.

### Impact Explanation
Any Bitcoin sent to a Serai deposit, branch, change, or forward address with a value in `[1, 9_999]` sats (or below `N::DUST` generally) is permanently locked at the threshold key. The deposit is never credited on Serai, and the protocol provides no recovery mechanism, so the underlying BTC is effectively burned even though the FROST group could technically sign for it. This directly satisfies "funds received that are not spendable" — the protocol treats them as nonexistent while they sit unspendable on the UTXO set. Severity: **Medium** — permanent loss of user funds, but capped per instance at the dust bound and requires a deposit below the threshold.

### Likelihood Explanation
Reachable by any unprivileged party sending a Bitcoin transaction to a Serai address — the exact "public inputs" in scope. Any user depositing an amount below 10,000 sats (plausible for small payments, test deposits, or partial-remainder sends) loses it silently. An attacker can also deliberately dust branch/forward addresses, though they only burn their own funds; the primary loss case is the unsuspecting depositor. No malicious validator, broken RPC, or collusion is required.

### Recommendation
Either document the dust bound as a hard minimum deposit and surface rejected sub-dust outputs to users (emit them in scan results with a flag so the coordinator can refund/reject explicitly), or accumulate sub-dust outputs so that a batch whose combined value exceeds `N::DUST` can be swept into a spendable `SignableTransaction`. At minimum, the silent `if output.balance().amount.0 >= N::DUST` filter in `processor/src/multisigs/scanner.rs:564` should log/track discarded outputs so locked funds are observable rather than invisible.

### Proof of Concept
```rust
// Conceptual, against networks/bitcoin + processor scanner path
// 1. Register a FROST key; scanner(key) registers External/Branch/Change/Forwarded offsets
//    (processor/src/networks/bitcoin.rs:314-347).
// 2. Send a real Bitcoin TX paying 9_999 sats to p2tr_script_buf(group_key)
//    (the External address).
// 3. get_outputs returns the ReceivedOutput (wallet::Scanner matches script_pubkey
//    with no value check, wallet/mod.rs:205-211).
// 4. processor/src/multisigs/scanner.rs:564 — 9_999 < Bitcoin::DUST (10_000) —
//    the output is silently dropped. No ScannerEvent, no scheduler input.
// 5. The 9_999 sats remain at the multisig key forever: SignableTransaction::new
//    can only consume outputs the scheduler knows about, and no sweep path exists.
```

*Note on completeness:* I verified the dust filter, the `DUST` constant, and the send/scanner paths; I did not exhaustively confirm whether `get_outputs` in `processor/src/networks/bitcoin.rs` uses `scan_block` (which would additionally admit immature coinbase outputs per the comment at `wallet/mod.rs:218-219`), as that call site was not fully inspected before the iteration limit.