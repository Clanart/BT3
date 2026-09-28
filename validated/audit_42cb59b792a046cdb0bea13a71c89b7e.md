### Title
Retired multisig key is dropped from the Scanner unconditionally, permanently locking any later-received outputs - (File: processor/src/multisigs/scanner.rs)

### Summary
Analogous to `LMPDestinations::removeFromRemovalQueue()` deleting a destination that still holds vault shares, Serai's multisig `Scanner` unconditionally removes a retired key from its scan set (`scanner.keys.remove(0)` / `ScannerDb::retire_key`) once the retirement block is reached — with no check that all funds sent to that key have been drained or that no further outputs can arrive. Any output sent to the retired key's address afterwards is never scanned, never reported, and unspendable by the processor.

### Finding Description
The scanner tracks active keys in `scanner.keys` and persists them via `ScannerDb::register_key`. When the scheduler signals `multisig_completed`, the retirement block is recorded as `block_number + N::CONFIRMATIONS` (`check_multisig_completed`, processor/src/multisigs/scanner.rs:663-668). When the scan reaches that block, the key is removed unconditionally:

- `scanner.rs:695-719`: `is_retirement_block` is computed from the stored retirement block, and `let retired = scanner.keys.remove(0).1;` drops the key and its `eventualities` entry with no balance/output check.
- `ack_block` (scanner.rs:350-353) then calls `ScannerDb::<N, D>::retire_key(txn)`, which deletes the key from `keys_key()` permanently (scanner.rs:120-125).

After removal, `network.get_outputs(&block, key)` (scanner.rs:550-567) is only invoked for keys in `scanner.keys`, so outputs paying to the retired key's script in any later block are invisible. This mirrors the audit finding: the "removal queue" equivalent (key retirement) is processed without verifying the entity is empty of funds, and unlike Tokemak — where shares at least remain detectable — here the funds are not even observed.

The retirement path in `processor/src/multisigs/mod.rs:608-617` asserts `existing.scheduler.empty()` for *planned* payments, but nothing covers externally-sent outputs arriving after the retirement block. The `ForwardFromExisting` handling (mod.rs:843-919) only forwards outputs seen *while the key is still registered*; once `retire_key` runs, the forwarding window closes permanently even though the old address remains valid on-chain and anyone can keep paying to it.

### Impact Explanation
Bitcoin addresses derived from the retired group key (external `Scalar::ZERO` offset address, and any registered Branch/Change/Forward offset scripts — see `processor/src/networks/bitcoin.rs:313-346`) remain valid and publicly known. Any user who sends funds to a stale address after the retirement block has those funds permanently locked: the scanner never emits a `ScannerEvent::Block` containing them, no refund or forward plan is created, and the threshold validators retain no scheduled path to spend them. This is a direct, unrecoverable loss of user/protocol funds — the same impact class as the source finding (funds locked in a removed-from-queue destination).

### Likelihood Explanation
Reachable by any unprivileged external party: sending a Bitcoin transaction to the previously-used deposit address requires no privileges, and users routinely re-use saved addresses. The window is unbounded — the old address works forever while the scanner watches it for only `CONFIRMATIONS` blocks post-completion. Medium likelihood, medium/high impact → Medium severity.

### Recommendation
Before retiring a key, verify no unscanned/unspent outputs can exist, and/or keep retired keys in a "watch-only" removal-queue state: continue scanning the retired key's scripts and emit refund/forward `PlanFromScanning` entries (the infrastructure already exists in `PlanFromScanning::Refund`/`Forward`, mod.rs:584-600) rather than deleting the key outright in `retire_key`/`scanner.keys.remove(0)`. At minimum, mark retired-key outputs as a distinct output type that is always refunded to a recoverable path instead of silently dropped.

### Proof of Concept
1. Multisig rotation completes; `check_multisig_completed` saves `retirement_block = block_number + CONFIRMATIONS` for the retiring key (scanner.rs:663-668).
2. Scanner scans the retirement block: `is_retirement_block` is true, `scanner.keys.remove(0)` and `scanner.eventualities.remove(...)` execute (scanner.rs:715-719); `ack_block` calls `retire_key`, deleting the key from the DB (scanner.rs:350-353, 120-125).
3. An external user sends BTC to the retired key's P2TR address (`p2tr_script_buf(retired_key)`).
4. In every subsequent block, `for (activation_number, key) in scanner.keys.clone()` (scanner.rs:550) no longer contains the retired key, so `network.get_outputs` is never called for it — the deposit is never detected, never assigned an offset, and no plan is created. The funds are locked despite the threshold group key still being derivable; no code path ever schedules spending them.

This maps the audit bug class: `retire_key`/`keys.remove(0)` is `removeFromRemovalQueue` — removal proceeds without the `balanceOf(...) == 0` guard, and the consequence is identically locked funds.