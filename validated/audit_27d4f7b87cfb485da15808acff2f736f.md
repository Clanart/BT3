### Title
OP_RETURN data output excluded from fee/weight calculation in `SignableTransaction::new`, causing underpaid fees, inflated change, and transactions that may not relay - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` computes the transaction's virtual size and required fee via `calculate_weight_vbytes`, which reconstructs the transaction from only `inputs`, `payments`, and an optional `change` script. However, when `data` is specified, an OP_RETURN output is pushed onto `tx_outs` *before* the size/fee calculation but is never passed into `calculate_weight_vbytes`. The result is a lossy size "conversion": the amount budgeted for fees (`needed_fee`) is computed on a smaller transaction than the one actually signed and broadcast — the exact analog of withdrawing a scaled-down amount (`amountPerp`) while accounting for the unscaled amount (`assets_`).

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `calculate_weight_vbytes` builds a transaction whose outputs consist solely of `payments` plus an optional `change` output [1](#0-0) . The OP_RETURN output is appended to `tx_outs` at lines 194-202, and then both the initial fee calculation (line 204) and the change-aware recalculation (lines 225-227) call `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only sees `payments` — never the data output [2](#0-1) .

Concretely, for a TX carrying `data`:
- `needed_fee = fee_per_vbyte * vbytes` understates the true required fee by `fee_per_vbyte * vsize(OP_RETURN output)` (~`fee_per_vbyte * (9 + data_len)` weight-derived vbytes).
- The minimum-relay-fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` uses the same understated `vbytes`, so a TX whose *actual* feerate is below the relay minimum can pass the check and be produced [3](#0-2) .
- When change exists, the change output is set to `input_sat - payment_sat - fee_with_change` where `fee_with_change` is also understated — so the change output is *larger* than it should be and the actual paid fee `sum(inputs) - sum(outputs)` is *smaller* than the intended `fee_per_vbyte` rate.
- The `weight > MAX_STANDARD_TX_WEIGHT` standardness check also omits the data output's weight, permitting a transaction that exceeds the standard weight limit [4](#0-3) .

Like the reference bug, the value actually committed on-chain (actual feerate, actual outputs) diverges from the value computed under the lossy transformation (vsize without the data output), and the divergence always favors the unscaled side: the transaction pays less than `needed_fee`-equivalent rate while `needed_fee()` reports the correct-looking figure.

### Impact Explanation
Any `SignableTransaction` created with `data` pays a lower feerate than requested. In the worst case the actual feerate falls below Bitcoin's default minimum relay fee (the guard is bypassed because it uses the same understated vbytes), producing a validly-signed transaction that peers reject — the burn/payment is signed and the inputs consumed from the wallet's perspective, yet the transaction cannot propagate or confirm. Even when it relays, callers relying on `needed_fee()` (e.g., `tx.needed_fee() == actual_fee` invariants as exercised in `networks/bitcoin/tests/wallet.rs` lines 269-272) observe a mismatch, and change is overpaid by the missing output's fee. This matches the accepted impact class: funds moved in a transaction whose on-chain form differs from what was accounted for, leaving payments effectively stuck.

### Likelihood Explanation
`data` is a public parameter of `SignableTransaction::new` and the OP_RETURN path is explicitly supported (data up to 80 bytes). Triggering requires only that a caller supply data — any caller-supplied instruction that attaches an OP_RETURN reaches the path with no privileged access. The miscalculation is deterministic whenever `data.is_some()`, not dependent on adversarial timing.

### Recommendation
Include the OP_RETURN output in the size/fee calculation. Construct the data output before calling `calculate_weight_vbytes` and pass the full output list (or add the data output's weight explicitly), for both the initial and change-aware computations:

```diff
-    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+    // calculate_weight_vbytes must account for the OP_RETURN output as well,
+    // e.g. by accepting the already-built tx_outs instead of just `payments`.
```

Alternatively, change `calculate_weight_vbytes` to take `&[TxOut]` so the data and change outputs are always included in weight/vbytes, the minimum-fee check, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
For `inputs` worth `I`, `payments` totaling `P`, `change` present, and `data = vec![0; 80]`:

1. `tx_outs` = payments + OP_RETURN(80 bytes) (lines 188-202).
2. `vbytes` computed over a tx of `len(payments)+1` outputs (change), missing the ~89-vbyte OP_RETURN output.
3. `needed_fee = fee_per_vbyte * vbytes` is ~`89 * fee_per_vbyte` sats too low; the change output absorbs those sats, so the *actual* feerate is `(needed_fee) / (vbytes + ~89)`, strictly below `fee_per_vbyte`.
4. If `fee_per_vbyte` is at the relay floor (e.g., 1 sat/vbyte → `needed_fee` barely passes `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`), the actual feerate falls below the floor and the signed transaction is rejected by the network, while the Serai-side plan believes the payment was made.

The existing test only exercises `data` on the no-change error path (`test_transaction_errors`, lines 185-191) and never asserts `fee() == needed_fee()` for a data-carrying transaction, so the discrepancy is uncaught.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-235)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
