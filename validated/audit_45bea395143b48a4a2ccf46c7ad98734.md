### Title
`SignableTransaction::new` computes weight/vbytes (and hence the fee and the min-relay-fee check) without the OP_RETURN output it adds, so the resulting transaction underpays relative to its true size - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class in the reference report is a sufficiency check evaluated against the wrong aggregate: `lf >= minPortion * lockedValues.total()` uses `cva + mm + lf` as the base when only `mm` is relevant, so the check is satisfied/failed for the wrong reasons. In `networks/bitcoin/src/wallet/send.rs`, the fee-sufficiency computation is evaluated against the wrong transaction: `calculate_weight_vbytes` is called with only `payments` (and optionally `change`), never including the OP_RETURN output that was already appended to `tx_outs`. The vbytes used for `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check therefore describe a transaction smaller than the one actually built and signed.

### Finding Description
In `SignableTransaction::new`, an OP_RETURN output carrying caller-supplied `data` is pushed onto `tx_outs` first: [1](#0-0) 

but the weight/vbytes are then computed only from `payments`, with no parameter for the data output: [2](#0-1) 

Inside `calculate_weight_vbytes`, the dummy transaction's `output` vector is built exclusively from `payments` plus an optional `change` output — the OP_RETURN output is structurally impossible to include since the function has no `data` parameter: [3](#0-2) 

Consequences, all evaluated against the wrong base transaction:

- `needed_fee = fee_per_vbyte * vbytes` omits the ~11+ vbytes of the OP_RETURN output (` [4](#0-3) `).
- The `TooLowFee` guard compares that already-underestimated `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` using the same underestimated `vbytes`, so it cannot catch the shortfall (` [5](#0-4) `).
- The `NotEnoughFunds` check and the change calculation both use this `needed_fee`/`fee_with_change` (` [6](#0-5) `), and `weight` used for the `MAX_STANDARD_TX_WEIGHT` check also excludes the data output (` [7](#0-6) `).
- The final transaction pushed into `SignableTransaction` includes the OP_RETURN output, so `fee() = sum(prevouts) − sum(outputs)` equals the under-computed `needed_fee` while the real transaction is larger (` [8](#0-7) `).

The data output is a legitimate public input to this API: `data: Option<Vec<u8>>` with any payload up to 80 bytes passes validation (` [9](#0-8) `). Each OP_RETURN output adds roughly `8 (value) + 1 (script len) + 1–2 (OP_RETURN + push opcode) + len(data)` bytes — up to ~92 vbytes with an 80-byte payload — entirely unaccounted for in the fee.

### Impact Explanation
A transaction constructed with `data: Some(...)` pays `fee_per_vbyte * vbytes` where `vbytes` excludes the OP_RETURN output, so its effective fee rate is strictly lower than requested. If `fee_per_vbyte` is at or near the minimum relay rate, the actual fee falls below `DEFAULT_MIN_RELAY_TX_FEE` for the true transaction size, producing a signed transaction that will not relay — unspendable/stuck inputs despite `SignableTransaction::new` reporting success and `needed_fee()` reporting a "sufficient" fee. At higher rates it silently underpays the intended fee rate (slower confirmation). Additionally, a transaction near `MAX_STANDARD_TX_WEIGHT` with a large data payload can pass the weight check while exceeding the standardness limit once the OP_RETURN output is included. This mirrors the reference finding: the sufficiency check is denominated against the wrong base (`total()` including `cva` there; a transaction missing its data output here), so the check does not guarantee what it claims.

### Likelihood Explanation
The bug triggers deterministically whenever `data` is `Some`. The primary in-tree caller passes `None` (`processor/src/networks/bitcoin.rs`), so exposure depends on consumers of the `bitcoin-serai` wallet API exercising the documented `data` parameter; within that parameter's domain the miscalculation is unconditional and requires no attacker sophistication — the API itself produces the under-fee'd transaction. Because the mis-sizing is bounded (~92 vbytes max), practical harm concentrates near the minimum-relay-fee boundary or the standardness weight limit, keeping this at Medium rather than higher.

### Recommendation
Include the OP_RETURN output in the size accounting: pass the data output (or its serialized length) into `calculate_weight_vbytes` so `vbytes`, `needed_fee`, the `TooLowFee` check, and the weight check all reflect the transaction actually being built, e.g. build the dummy `output` vector from `tx_outs` (payments + OP_RETURN) plus the optional change output rather than from `payments` alone.

### Proof of Concept
Conceptual, against `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

1. Call `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte * vbytes_no_data` just exceeds `DEFAULT_MIN_RELAY_TX_FEE * vbytes_no_data / 1000` — the `TooLowFee` check passes.
2. The returned transaction's `tx.output` contains the payments plus a ~92-byte OP_RETURN output that was never measured; its real vsize is `vbytes_no_data + ~92` while it pays only `fee_per_vbyte * vbytes_no_data` (visible via `fee()`).
3. The effective fee rate is `needed_fee / actual_vsize < fee_per_vbyte`; at the boundary this is below `DEFAULT_MIN_RELAY_TX_FEE` (1000 sat/kvB), so the signed transaction the wallet produces is rejected by relay policy — funds committed to inputs cannot move — despite all internal sufficiency checks passing, because each check was computed against a transaction missing one of its outputs.

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-213)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-235)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```
