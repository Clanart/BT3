### Title
SignableTransaction omits the OP_RETURN data output from its weight/fee calculation, letting a crafted `data` payload push the effective fee rate below the minimum relay policy - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output carrying caller-supplied `data` to `tx_outs`, but computes the transaction weight and required fee via `calculate_weight_vbytes`, which is only given `payments` — the data output is never included. The `TooLowFee` policy check and the change-output math are therefore both computed against an under-weight transaction, so a transaction with an ~89-byte data output can pass the minimum-relay-fee check while its actual fee rate falls below it. This is the direct analog of the CVE's bug class: a policy check (minimum fee rate) is enforced against a sanitized model of the object while the real object contains attacker-influenced bytes that bypass the restriction.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- `SignableTransaction::new` appends the `OP_RETURN` output to `tx_outs` when `data` is provided (lines 194–202).
- The weight used for both `needed_fee` and the minimum-fee check is computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) — `payments` does not contain the data output.
- `calculate_weight_vbytes` builds a model `Transaction` solely from `payments` and an optional `change` script (lines 85–99); there is no parameter for the data output at all.
- The policy gate `if needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) and the change feasibility check `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228) both use this understated `vbytes`.

An 80-byte `data` payload adds a ~89-vbyte output (~9-byte script + 8-byte value + compactsize, i.e. ~97 weight units ≈ 24–25 vbytes after segwit discounting — here it's all base size so ~89 vbytes) that is completely uncounted. The signed transaction's true vsize exceeds `vbytes` by that amount, so:

- `fee / actual_vsize` is strictly less than the caller-requested `fee_per_vbyte`, and can drop below `DEFAULT_MIN_RELAY_TX_FEE` even though the check passed.
- When change is emitted, the change output's value is over-credited by `fee_per_vbyte * ~89` sats worth of vsize that was never charged, making the *actual* fee rate diverge further from the requested rate.

The `data` parameter is attacker-influenceable caller input (bounded only by the 80-byte `TooMuchData` cap at line 171), so an unprivileged party that can cause a data-carrying BTC transaction to be constructed controls the magnitude of the fee-policy bypass.

### Impact Explanation
The transaction is still validly signed and consensus-valid, but its effective fee rate is lower than requested and can be below Bitcoin's default minimum relay fee despite `SignableTransaction` explicitly claiming to enforce that floor. On-network this produces transactions that fail to relay/confirm, leaving inputs (multisig funds) tied up in an unconfirmable transaction until replaced — a material availability/funds-frozen impact. At minimum the wallet silently violates its own documented fee policy (`needed_fee` no longer reflects the fee the transaction actually needs at the requested rate), which is an integrity failure of the policy boundary this function exists to enforce. Rated Medium: reachable purely from public input bytes (`data`), deterministic, but impact is degraded transaction propagation/fee underpayment rather than theft or forgery.

### Likelihood Explanation
Any caller of `SignableTransaction::new` that supplies `data` triggers the miscalculation unconditionally — no race, no collusion, no special chain state required. The bypass only turns into a relay failure when the requested `fee_per_vbyte` is near the minimum (e.g., `fee_per_vbyte = 1`–`2` with a large payload), and the scheduler path in `processor/src/networks/bitcoin.rs` currently passes `None` for `data`, so deployment impact depends on integrators using the data field. As a library-level defect it is deterministic whenever the API is used as documented.

### Recommendation
Include the data output in the weight model: either pass the fully constructed `tx_outs` (including the `OP_RETURN` output) into `calculate_weight_vbytes`, or add the serialized size of the `OP_RETURN` `TxOut` to the computed weight/vbytes before deriving `needed_fee`, `fee_with_change`, and performing the `TooLowFee` check.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style
let inputs = vec![output]; // a scanned ReceivedOutput
let data = vec![0u8; 80];  // max permitted payload
let tx = SignableTransaction::new(inputs, &[], None, Some(data), 1 /* sat/vbyte */).unwrap();

// The actual transaction includes an ~89-byte OP_RETURN output...
let actual_vsize = u64::try_from(tx_clone.vsize()).unwrap(); // e.g. ~154 + 89
// ...but needed_fee() was computed without it:
assert_eq!(tx.needed_fee(), 1 * (actual_vsize - 89)); // under-modelled vsize
// Actual fee rate paid:
let actual_rate = tx.needed_fee() / actual_vsize; // == 0 sats/vbyte < DEFAULT_MIN_RELAY_TX_FEE/1000
// The TooLowFee check at send.rs:211 passed anyway, so a below-minimum-fee
// transaction is produced that the default mempool policy will reject.
```

Root cause: `calculate_weight_vbytes` at `networks/bitcoin/src/wallet/send.rs:62-127` has no way to account for the `OP_RETURN` output appended at `send.rs:194-202`, and both the fee derivation (`send.rs:204-213`) and change math (`send.rs:224-235`) use that under-counted weight.