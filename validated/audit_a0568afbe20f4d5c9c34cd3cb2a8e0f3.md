### Title

Unattributed payments to internal Bitcoin output types can be received but never scheduled for spending - (File: processor/src/multisigs/mod.rs)

### Summary

The Bitcoin scanner deterministically derives four output types for every multisig key: `External`, `Branch`, `Change`, and `Forwarded`. Any Bitcoin transaction paying to the `Branch`, `Change`, or `Forwarded` Taproot script is successfully detected and represented as a spendable `ReceivedOutput`, but the block-processing path removes every non-`External` output before refund/instruction handling. Under the normal rotation step, these attacker-created internal-looking outputs are consequently neither reported as deposits nor reliably inserted into the scheduler’s spendable UTXO set. The result is an analog of assets being accepted by the protocol without a redemption path: Bitcoin sent to a public, deterministic internal address can remain recognized on-chain but never be scheduled for spending.

### Finding Description

`Bitcoin::get_outputs` constructs a `Scanner` for the multisig key and registers fixed offsets for `Branch`, `Change`, and `Forwarded`, while offset zero maps to `External`. The scanner detects every transaction output whose `script_pubkey` matches one of those registered scripts and returns a `ReceivedOutput` carrying the offset needed to spend it.

The issue is that classification is based only on the script. An unprivileged Bitcoin sender can compute the deterministic internal offsets and send to a branch, change, or forwarding address. The scanner will detect that output as the corresponding internal `OutputType`, but `scanner_event_to_multisig_event` then explicitly retains only `OutputType::External` before creating deposit instructions, forwarding plans, or refunds. Non-`External` outputs are therefore filtered out of the normal deposit-processing path.

`Branch` outputs can only trigger pending scheduled plans if the scheduler already expects a branch output of that exact amount. `Change` outputs are only retained during particular rotation handling when the transaction is linked to a previously resolved plan. `Forwarded` outputs only reconstruct an instruction if a matching amount was previously saved in `ForwardedOutputDb`. An arbitrary third-party payment to one of these deterministic internal addresses satisfies none of those provenance conditions, even though the underlying UTXO is spendable by the threshold key using its scanned offset.

### Impact Explanation

An attacker can cause Bitcoin to become effectively stranded inside Serai’s accounting by paying to a computable internal output address rather than the external deposit address. The UTXO exists on-chain and is detectable by Serai, but it is excluded from normal external-output processing and does not necessarily enter the scheduler as spendable inventory. The deposited amount may therefore never be credited, refunded, forwarded, or incorporated into a future transaction.

This is a loss-of-funds / unavailable-funds condition rather than a cryptographic key compromise. The protocol has the technical signing capability to spend the output, but its scanner-to-scheduler flow provides no reliable recovery path for unexpected internal-type outputs.

### Likelihood Explanation

The attack requires only a standard Bitcoin transaction from an unprivileged sender. The group key is public, the three internal offsets are deterministic `hash_to_F` values under the fixed `"Serai Bitcoin Output Offset"` domain, and the corresponding Taproot addresses are exposed through `branch_address`, `change_address`, and `forward_address`. No malicious validator, peer, RPC response, leaked key, or invalid encoding is required.

The affected amount is attacker-chosen. A sender seeking to grief Serai or accidentally paying to the wrong derived address can create an output that is cryptographically controlled by the multisig but ignored by the deposit handling path.

### Recommendation

Maintain a durable recovery queue for all recognized UTXOs, including unexpected `Branch`, `Change`, and `Forwarded` outputs. At minimum, non-`External` outputs that do not match a pending branch plan, resolved change eventuality, or saved forwarded-output record should be inserted into the scheduler’s UTXO pool or emitted as an explicit operational anomaly requiring a recovery plan.

The scanner should also distinguish provenance where possible instead of relying solely on `script_pubkey` classification. For example, track expected self-created outpoints and classify unrecognized payments to internal scripts separately from protocol-generated internal outputs.

### Proof of Concept

```rust
// Conceptual Bitcoin-side trigger.

// Serai derives the internal addresses deterministically:
let (_, offsets, _) = scanner(group_key);

let branch_addr =
    address_from_key(group_key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Branch]));
let change_addr =
    address_from_key(group_key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Change]));
let forward_addr =
    address_from_key(group_key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Forwarded]));

// An unprivileged sender broadcasts a normal P2TR payment to `change_addr`.
// Scanner::scan_transaction detects it and maps the script back to the
// `Change` offset.
let scanned = scanner.scan_transaction(&attacker_tx);
assert_eq!(scanned.len(), 1);
assert_eq!(scanned[0].offset(), offsets[&OutputType::Change]);

// Bitcoin::get_outputs wraps it as Output { kind: OutputType::Change, ... }.
//
// scanner_event_to_multisig_event subsequently executes:
//
//   outputs.retain(|output| output.kind() == OutputType::External);
//
// The attacker-created change output is discarded from deposit instruction
// processing. Unless internal scheduler provenance separately retains it, the
// UTXO is not credited and is not guaranteed to be scheduled for a later spend.
```

Relevant code:

- `processor/src/networks/bitcoin.rs:308-348` derives fixed `External`, `Branch`, `Change`, and `Forwarded` offsets and constructs the scanner.
- `processor/src/networks/bitcoin.rs:686-740` scans blocks and tags outputs by offset.
- `networks/bitcoin/src/wallet/mod.rs:180-214` maps a matching `script_pubkey` to a spendable `ReceivedOutput`.
- `processor/src/multisigs/mod.rs:823-838` drops all non-`External` outputs from instruction handling.
- `processor/src/multisigs/scheduler/utxo.rs:266-301` only gives `Branch` outputs special treatment when a queued branch plan exists; otherwise, outputs are pushed into the UTXO set only if they reach `add_outputs`.
- `networks/bitcoin/src/wallet/send.rs:273-284` confirms that a scanned offset is sufficient for the threshold keys to authorize spending the corresponding output.