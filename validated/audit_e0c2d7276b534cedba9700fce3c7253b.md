### Title
Minimum-fee and max-weight checks are computed on a transaction smaller than the one actually signed, allowing a sub-minimum-fee / non-standard transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output for caller-supplied `data` to `tx_outs`, but then computes `weight`, `vbytes`, and therefore `needed_fee` via `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which only includes `inputs` and `payments` — never the data output. The `TooLowFee` check (fee must meet `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) and the `TooLargeTransaction` check (`weight <= MAX_STANDARD_TX_WEIGHT`) are thus evaluated against a strictly smaller transaction than the one produced and signed. This is the same bug class as the reference report: a minimum/threshold check is validated at one value, while the committed value is effectively lower (here, the effective fee rate) or larger (weight).

### Finding Description
In `SignableTransaction::new`:

1. The OP_RETURN output is pushed to `tx_outs` before any size accounting (send.rs:193-202). [1](#0-0) 
2. `calculate_weight_vbytes(tx_ins.len(), payments, None)` builds a dummy tx containing only inputs and payment outputs — no `data` output — to derive `weight` and `vbytes` (send.rs:204, 85-94). [2](#0-1) 
3. `needed_fee = fee_per_vbyte * vbytes` and the minimum-fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` use the under-counted `vbytes` (send.rs:206-213). [3](#0-2) 
4. The change branch re-derives size via `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` — still without the data output (send.rs:224-234). [4](#0-3) 
5. The final `weight > MAX_STANDARD_TX_WEIGHT` check uses the same data-excluding `weight` (send.rs:241-243). [5](#0-4) 
6. The returned `SignableTransaction` embeds `tx_outs`, which does include the OP_RETURN output (send.rs:245-251). [6](#0-5) 

`data` is attacker-/caller-controlled transaction data (up to 80 bytes per the `TooMuchData` check at send.rs:171-173), so an unprivileged party who can cause a transaction with an OP_RETURN payload to be signed reaches this path entirely with public inputs. [7](#0-6) 

### Impact Explanation
The actual fee paid is `sum(inputs) - sum(outputs)` (send.rs:137-141), i.e. `needed_fee`, which was sized for the smaller transaction. The real transaction is up to ~83+ bytes larger (OP_RETURN output: value, script length, up to 80 bytes of pushes), so the realized fee rate is `needed_fee / actual_vsize`, which can fall below both the caller-requested `fee_per_vbyte` and the Bitcoin minimum relay fee of 1 sat/vbyte — exactly the undercut-the-minimum pattern of the reference finding. Additionally, a transaction sized just under `MAX_STANDARD_TX_WEIGHT` in the check can exceed it once the data output is serialized, producing a signed transaction that is non-standard and will not relay. The threshold signatures commit to the real (larger) transaction, so a signed, sub-minimum-fee or overweight transaction is produced that honest nodes will refuse to propagate, stalling the payout and locking the inputs until abandoned.

### Likelihood Explanation
Triggering requires `fee_per_vbyte` close to the 1 sat/vbyte floor and a `data` payload: e.g. `fee_per_vbyte = 1` passes the check on the reduced `vbytes`, but the ~90-byte data output inflates vsize so the effective rate drops below 1 sat/vbyte. Likewise, a payments set near the weight limit plus a data output crosses `MAX_STANDARD_TX_WEIGHT` undetected. Both conditions are reachable with legitimately constructed inputs, though they require operating near the boundaries, which bounds the likelihood to medium-low.

### Recommendation
Include the data output in the size accounting: build the OP_RETURN `TxOut` first and pass all outputs (payments + optional OP_RETURN) into `calculate_weight_vbytes`, or add a constant upper-bound size for the OP_RETURN output to `weight`/`vbytes` before computing `needed_fee` and performing the `TooLowFee` and `TooLargeTransaction` checks. Recompute both checks against the final `tx_outs` actually committed to the transaction.

### Proof of Concept
```rust
// In SignableTransaction::new (send.rs), with data = vec![0; 80]:
// tx_outs gets an extra OP_RETURN output (~91 serialized bytes).
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// vbytes excludes the OP_RETURN output.
let mut needed_fee = fee_per_vbyte * vbytes;
// With fee_per_vbyte == 1: needed_fee == vbytes, passes
// needed_fee < (1000 * vbytes) / 1000  ==>  vbytes < vbytes  ==> false, accepted.
// The signed tx, however, has vsize' = vbytes + ~91 bytes, so its
// effective fee rate = vbytes / vsize' < 1 sat/vbyte, below
// DEFAULT_MIN_RELAY_TX_FEE, and it will not be relayed.
// Similarly, if weight is just under MAX_STANDARD_TX_WEIGHT,
// the additional ~364 WU of the data output makes the signed tx
// exceed the standardness limit while passing the check at line 241.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
```rust
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
