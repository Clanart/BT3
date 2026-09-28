### Title
Bitcoin sent to Serai's Branch/Change/Forwarded addresses is permanently lost - (File: processor/src/multisigs/mod.rs, processor/src/networks/bitcoin.rs)

### Summary
The bug class in the external report is "funds sent along a legitimate interface, but not matching the expected usage, are silently absorbed instead of being rejected or refunded." Serai's Bitcoin integration exhibits this: any external party can derive the multisig's internal-use addresses (Branch, Change, Forwarded) and send BTC to them. The processor scans these outputs but then silently discards them — no instruction is emitted, no refund is scheduled, and the output is dropped from the spendable UTXO set, permanently freezing the coins in the multisig.

### Finding Description
Serai derives all four of its Bitcoin address roles from the same group key using deterministic, public offsets: `hash_to_F("Serai Bitcoin Output Offset", b"branch" / "change" / "forward")` (networks/bitcoin/src/lib.rs/registration in `processor/src/networks/bitcoin.rs:308-347`). These offset scalars are fixed constants, so any unprivileged user can compute `p2tr_script_buf(key + G*offset)` for the Branch, Change, and Forwarded addresses and pay to them.

`get_outputs` scans all four script types and tags each output with its `OutputType` (processor/src/networks/bitcoin.rs:686-700). However, downstream processing discards non-External outputs received from outside:

- In `scanner_event_to_multisig_event` (processor/src/multisigs/mod.rs:824-837), `Forwarded` outputs only produce an instruction if a matching entry exists in `ForwardedOutputDb` (i.e., it was an expected internal forward); otherwise they are dropped, and then all non-`External` outputs are filtered out entirely: `outputs.retain(|output| output.kind() == OutputType::External)`.
- In the `existing_outputs.retain` logic (processor/src/multisigs/mod.rs:485-551): `External | Forwarded => false`, `Branch` is only scheduled if the scheduler can use that exact balance, and `Change` is only kept if the containing transaction resolved a known `Plan` with self-change (`PlanDb::plan_by_key_with_self_change`).

So a user who mistakenly (or a third party who deliberately) sends BTC to the Change or Branch address — which look like ordinary Serai addresses and are trivially computable — produces an output that is detected by the scanner, emitted in a `ScannerEvent::Block`, and then silently dropped from every code path. There is no validation step that refuses such payments or routes them to a refund; the UTXO remains under the multisig key but is never scheduled, aggregated, or credited.

### Impact Explanation
Permanent loss of the sender's BTC: the output is spendable only by the threshold multisig, yet the processor's scheduling logic never includes it in a `Plan` (Branch outputs not matching `scheduler.can_use_branch` are discarded; unsolicited Change outputs fail `plan_by_key_with_self_change`). Analogous to ETH locked in InfinityExchange when sent with an ERC20 `takeOrders` call — the funds sit under protocol control with no code path that can ever move them. This is a direct fund-freeze medium-severity issue.

### Likelihood Explanation
Conditional on user error, same as the original finding: the internal addresses are standard P2TR outputs computable by anyone (the offsets are deterministic `hash_to_F` results), so a wallet/user copying a previously-seen Serai address (e.g., a Branch or Change output visible on-chain) can pay to it. The same scan interface is used for deposits and internal outputs, making accidental misuse plausible.

### Recommendation
Treat unsolicited non-External outputs as refundable/forwardable: e.g., route any `Change`/`Branch`/`Forwarded` output whose `tx` does not resolve a known plan through `instruction_from_output`'s refund path (`PlanFromScanning::Refund` to `presumed_origin`), or aggregate them into the spendable UTXO pool rather than dropping them in `existing_outputs.retain`.

### Proof of Concept
1. An external observer computes `CHANGE_OFFSET = hash_to_F("Serai Bitcoin Output Offset", b"change")` (public constant logic in `processor/src/networks/bitcoin.rs:308-347`) and builds the change address `p2tr_script_buf(group_key + G*CHANGE_OFFSET)` — or simply copies it from a prior on-chain transaction.
2. The user sends BTC to that address in a normal transaction.
3. `Bitcoin::get_outputs` scans it as `OutputType::Change` (bitcoin.rs:686-700); the scanner emits it in `ScannerEvent::Block`.
4. `scanner_event_to_multisig_event` filters it out via `outputs.retain(|o| o.kind() == OutputType::External)` (mod.rs:837) — no instruction, no refund.
5. In the retain logic (mod.rs:527-549), the Change output is kept only if `ResolvedDb`/`PlanDb` link its tx to a plan with self-change; for an external payment this fails and it is discarded.
6. The BTC remains spendable solely by the multisig but is never scheduled — permanently frozen.