### Title
Fee and weight checks computed on a transaction missing the OP_RETURN `data` output, so the signed transaction is larger and pays a lower feerate than the checked bounds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends the OP_RETURN output built from the caller-supplied `data` to `tx_outs` (lines 194-202) but computes `weight`, `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only models the `payments` outputs and an optional change output — never the data output. The transaction that is actually signed therefore carries an extra output that none of the bounding checks account for, directly analogous to comparing a limit expressed in one quantity against a value measured in a different quantity.

### Finding Description
- The OP_RETURN output is pushed onto `tx_outs` before any sizing is done, at `send.rs:194-202`.
- `calculate_weight_vbytes` (send.rs:62-127) builds a mock `Transaction` containing only `payments` outputs plus an optional change output; `data` is never passed in, so `weight`/`vbytes` at line 204 and `weight_with_change`/`vbytes_with_change` at lines 225-226 all exclude the OP_RETURN output (≈ 92 non-witness bytes ≈ 368 WU ≈ 92 vbytes for an 80-byte payload).
- Consequences of the mismatch:
  - `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) understate the fee required to achieve the requested `fee_per_vbyte`. The actual fee paid is `input_sat - outputs` = `needed_fee`, so the broadcast transaction's real feerate is `needed_fee / actual_vbytes < fee_per_vbyte`.
  - The `TooLowFee` check at lines 207-213 validates `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same understated `vbytes`. A transaction that passes this check can have a real feerate below the 1 sat/vB relay minimum once the OP_RETURN output is included, so peers/nodes will reject it.
  - The `TooLargeTransaction` check at lines 241-243 compares a `weight` that excludes the data output against `MAX_STANDARD_TX_WEIGHT`; a real transaction up to ~368 WU over the standardness limit can pass the check and be signed, producing a non-standard transaction no node will relay.
  - Similarly, `NotEnoughFunds` (line 215) is evaluated against the understated fee, so a plan can be judged fundable when it is not once the true size is priced.

### Impact Explanation
Any caller that supplies `data` (a public input to `SignableTransaction::new`, up to 80 bytes) causes the multisig to produce a signed transaction whose committed fee is lower than the requested `fee_per_vbyte` and whose size/weight exceed what the safety checks validated. At the margin this yields transactions below the minimum relay feerate or above `MAX_STANDARD_TX_WEIGHT` — i.e., funds locked in the threshold address are committed to a transaction Bitcoin nodes will not relay or mine, stalling withdrawals until a new plan is built. This is a Medium-severity incorrect-check bug: an unprivileged party supplying transaction data produces a signed transaction violating the bounds the code claims to enforce.

### Likelihood Explanation
The bug triggers deterministically whenever `data.is_some()`; no special conditions are needed for the fee understatement. Relay-minimum violations occur whenever `fee_per_vbyte * 92` shifts the effective rate below 1 sat/vB, i.e., for low fee rates; standardness-limit violations require the tx to be within ~370 WU of the 400,000 WU cap, which is plausible for large consolidation transactions with many inputs/outputs.

### Recommendation
Include the data output in the size model: pass the constructed `tx_outs` (or the OP_RETURN output explicitly) into `calculate_weight_vbytes` so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, the `TooLowFee` minimum, and the `MAX_STANDARD_TX_WEIGHT` check are all evaluated against the exact transaction that will be signed. Alternatively, compute weight from `tx` after `tx_outs` is finalized instead of reconstructing a mock transaction.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style test
let inputs = vec![output]; // ReceivedOutput with value V
let addr = p2tr_script_buf(key).unwrap();
let data = vec![0u8; 80];

// Passes TooLowFee: needed_fee = 1 * vbytes(payments-only tx)
let st = SignableTransaction::new(
  inputs, &[(addr.clone(), 546)], None, Some(data), 1 /* fee_per_vbyte */,
).unwrap();

// Actual signed tx contains the extra OP_RETURN output (~92 extra vbytes),
// so fee()/actual_vbytes < 1 sat/vB: below DEFAULT_MIN_RELAY_TX_FEE.
// The too-low-fee check at send.rs:211 used vbytes excluding the data output.
assert!(st.fee() < st.transaction().vsize() as u64); // effective rate < 1 sat/vB
```