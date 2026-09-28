### Title
`SignableTransaction::new` validates fee and weight against a transaction that omits the OP_RETURN output - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to CVE-2026-43093 — where the kernel's `xdp_umem_reg()` validated headroom without reserving space for the tailroom/trailing metadata — `SignableTransaction::new` appends an OP_RETURN output to `tx_outs` but then calculates weight, vbytes, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check using `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which enumerates only `payments` and never sees the data output it will actually ship.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- The OP_RETURN output is pushed onto `tx_outs` before any sizing is done (lines 193-202). [1](#0-0) 
- The weight/vbyte computation, however, reconstructs a transaction purely from `inputs` and `payments` plus an optional `change` output — the OP_RETURN output is never represented (lines 85-99, 204). [2](#0-1) [3](#0-2) 
- Consequently `needed_fee = fee_per_vbyte * vbytes` under-charges (line 206), the minimum-relay-fee check uses the too-small `vbytes` (lines 211-213), the `NotEnoughFunds` check under-reserves (line 215), and the change computation `input_sat - payment_sat - fee_with_change` grants change output value that should have been fee (lines 224-234). [4](#0-3) 
- The `TooLargeTransaction` check compares the under-counted `weight` (which can exclude the OP_RETURN output entirely when no change is added, since `weight` is only overwritten in the change branch) against `MAX_STANDARD_TX_WEIGHT` (line 241). [5](#0-4) 

So the produced `Transaction` in `tx` includes an extra ~90-byte OP_RETURN output (8-byte value + compactsize + `OP_RETURN` push of up to 80 bytes) whose ~360+ weight units were never budgeted — precisely the class of "headroom validated without accounting for space actually consumed".

### Impact Explanation
Two concrete outcomes for a `SignableTransaction` built with `data: Some(..)`:

1. The declared `needed_fee` and the change amount are computed against a smaller vsize than the real transaction. The change output absorbs the shortfall, so the transaction's *actual* fee rate is lower than `fee_per_vbyte` and can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` guard passed — producing a signed transaction that will not relay, freezing the spent inputs until a corrected transaction is produced.
2. A transaction sized just under `MAX_STANDARD_TX_WEIGHT` by `payments` alone can exceed 400,000 WU once the OP_RETURN output is included, passing `TooLargeTransaction` yet yielding a non-standard, unbroadcastable transaction — funds the scheduler believes it paid out are not actually movable on chain.

Both satisfy the "funds reported sent/received that are not spendable" and "incorrect formula" acceptance criteria. Severity: Medium.

### Likelihood Explanation
The bug triggers whenever `SignableTransaction::new` is invoked with `data: Some(_)`, which is part of the public wallet API; the excess is deterministic (OP_RETURN output is up to ~92 bytes / ~368+ weight units). It does not require exotic inputs — any caller combining near-limit payments with a data output, or relying on the declared fee rate, hits it. The processor's own `make_signable_transaction` currently passes `None` for `data`, so exploitation requires a caller that supplies data (a supported, documented parameter of the API). Reachability by an unprivileged party depends on integration, hence Medium rather than High.

### Recommendation
Build the OP_RETURN output before sizing and pass the complete output set (payments + OP_RETURN + optional change) into `calculate_weight_vbytes` — e.g., give it a `tx_outs: &[TxOut]` parameter instead of reconstructing outputs from `payments`. Recompute both the no-change and with-change variants over the full output list so `needed_fee`, `NotEnoughFunds`, the change amount, and `MAX_STANDARD_TX_WEIGHT` all account for the data output's weight.

### Proof of Concept
```rust
// networks/bitcoin: construct a transaction whose real vsize exceeds
// what needed_fee() was charged for, and whose weight check was evaded.

let data = vec![0u8; 80]; // maximum allowed payload

// payments sized so payments-only weight is just under MAX_STANDARD_TX_WEIGHT
let payments = vec![(addr(), 1000); N];

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), FEE).unwrap();

// The OP_RETURN output is present in the final transaction...
assert_eq!(tx.tx.output.len(), N + 1);

// ...yet its weight was never counted:
// real weight = payments_weight + op_return_weight > measured `weight`
assert!(tx.tx.weight().to_wu() > measured_weight_used_for_check);
// therefore real fee rate = (inputs - outputs) / tx.vsize() < FEE
```
Concretely: `weight`/`vbytes` returned from `calculate_weight_vbytes(tx_ins.len(), payments, None)` never include the `TxOut { value: 0, script_pubkey: ScriptBuf::new_op_return(...) }` pushed at line 195, while `fee()` (inputs minus outputs) is charged on the final, larger transaction — proving the validation operated on a different (smaller) transaction than the one produced.

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-202)
```rust
    // Add the OP_RETURN output
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-204)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-235)
```rust
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
