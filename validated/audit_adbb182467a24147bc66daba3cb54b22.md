### Title
Fee and weight calculations omit the OP_RETURN output, breaking the measured-vs-actual transaction shape - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes transaction weight/vbytes — which drive `needed_fee`, the `TooLowFee` minimum-relay check, the change-output amount, and the `TooLargeTransaction` check — over a synthetic transaction that includes only the payment outputs (and optionally change). When the caller supplies `data`, an OP_RETURN output is appended to `tx_outs` *after* the vbytes are computed, so all fee/weight decisions are made against a transaction that is missing a real output. This is the same bug class as Plaza's USD/USDC mismatch: a quantity is computed over one representation (the transaction without OP_RETURN, i.e. USD-denominated TVL) and then applied to another (the actual broadcast transaction, i.e. USDC-denominated bond valuation), silently breaking the protocol's invariants.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed to `tx_outs` at send.rs:194-202, but both calls to `Self::calculate_weight_vbytes` (lines 204 and 225-226) pass only `payments` (and `change`) — `payments` here is the caller's payment slice, which never contains the OP_RETURN output. `calculate_weight_vbytes` (lines 62-127) reconstructs a `Transaction` solely from `payments`/`change` and returns its weight and vsize.

Concretely, with `data` of length `n` (up to 80 bytes), the actual transaction contains an extra `TxOut` of roughly `8 (value) + 1 (script len) + 1+n (OP_RETURN push)` bytes, all in base size (≈ 4 weight units per byte, ~40–90 vbytes underestimate).

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) under-charges relative to the real transaction size, so the actual paid fee rate is `fee_per_vbyte * (vbytes / real_vbytes)` — below the caller-specified rate.
2. The `TooLowFee` check at line 211 validates `needed_fee` against the underestimated `vbytes`, so a transaction whose *real* fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` (i.e. `needed_fee < real_vbytes` sats when `fee_per_vbyte == 1`) is accepted and produced — it will be rejected by relay/mempool.
3. The `TooLargeTransaction` check at line 241 compares the underestimated `weight` against `MAX_STANDARD_TX_WEIGHT`; a transaction just under the limit that also carries `data` can exceed 400,000 WU in reality and be non-standard.
4. When `change` is present, the change amount `input_sat - (payment_sat + fee_with_change)` is computed with the underestimated `fee_with_change`, so the change output is slightly over-funded at the expense of fee rate — compounding issue 1.

`data` is an unprivileged public input (any byte string ≤ 80 bytes) to `SignableTransaction::new`, and the resulting `SignableTransaction`/`TransactionMachine` drives FROST signing of the malformed transaction.

### Impact Explanation
An unprivileged party that can cause a `SignableTransaction` to be built with `data` (e.g. an OP_RETURN-bearing withdrawal/plan) obtains a signed Bitcoin transaction whose effective fee rate is lower than intended — potentially below the default minimum relay fee, so the transaction cannot propagate, stalling funds in the threshold wallet — or whose real weight exceeds `MAX_STANDARD_TX_WEIGHT`, making it permanently non-standard despite passing `TooLargeTransaction`. Either way the signature is produced over a transaction that violates the policy invariants the constructor was supposed to enforce. Medium severity: no direct theft, but signed transactions that are unbroadcastable/non-standard constitute broken protocol mechanics with funds unspendable until a corrected transaction is produced.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is invoked with `Some(data)` — a documented public input (used for OP_RETURN metadata). The miscalculation is deterministic: the OP_RETURN bytes are always present in the real transaction but always absent from the measured one. It triggers whenever the fee/weight margins matter: `fee_per_vbyte` near the relay minimum (1 sat/vb), or transactions near the max standard weight, or change values near `DUST`.

### Recommendation
Include the OP_RETURN `TxOut` in the transaction reconstructed inside `calculate_weight_vbytes` (e.g. pass the already-built `tx_outs`, or append the OP_RETURN output before measuring), so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction that will actually be signed and broadcast. Alternatively, compute `needed_fee`/`fee_with_change` from the final `tx`'s real weight after construction.

### Proof of Concept
```rust
// networks/bitcoin context: SignableTransaction::new(inputs, payments, change, data, fee_per_vbyte)
// With data = Some(vec![0u8; 80]) and fee_per_vbyte = 1:

let payments = vec![(p2tr_script_buf(key).unwrap(), 10_000u64)];
let data = Some(vec![0u8; 80]);
let stx = SignableTransaction::new(vec![output], &payments, None, data.clone(), 1).unwrap();

// stx.needed_fee() = 1 * vbytes(tx WITHOUT the ~90-byte OP_RETURN output)
// stx.transaction().vsize() = vbytes WITH the OP_RETURN output, ~90 vb larger
assert!(stx.needed_fee() < u64::try_from(stx.transaction().vsize()).unwrap());
// Actual paid fee rate = needed_fee / real_vsize < 1 sat/vb < DEFAULT_MIN_RELAY_TX_FEE,
// so the signed transaction is produced yet will fail standard relay checks.
// Similarly, `weight` used for the MAX_STANDARD_TX_WEIGHT check omits the
// OP_RETURN output's ~360 WU, letting an oversized transaction pass.
```

Relevant code: `SignableTransaction::new` builds the OP_RETURN `TxOut` into `tx_outs` but excludes it from both `calculate_weight_vbytes` invocations that determine `needed_fee`, `weight`, the `TooLowFee` check, and the `TooLargeTransaction` check.