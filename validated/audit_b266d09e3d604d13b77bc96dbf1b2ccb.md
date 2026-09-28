### Title
`SignableTransaction` fee and weight calculation omits the OP_RETURN data output, so the signed transaction pays a lower fee rate than requested and may be unrelayable / overweight - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Allo `Transfer.sol` report — where leftover value is not accounted for and the accounting check produces incorrect results — `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` misaccounts for an output it creates. The constructor pushes an OP_RETURN output carrying `data` onto `tx_outs`, but `calculate_weight_vbytes` models the transaction using only `payments` and `change`, so the weight/vbytes (and therefore `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check) are computed for a transaction that is smaller than the one actually built and signed.

### Finding Description
In `SignableTransaction::new`:

1. The OP_RETURN output is appended to the real outputs at `send.rs:194-202`:
   ```rust
   if let Some(data) = data {
     tx_outs.push(TxOut {
       value: Amount::ZERO,
       script_pubkey: ScriptBuf::new_op_return(
         PushBytesBuf::try_from(data).expect("...")),
     })
   }
   ``` [1](#0-0) 

2. Fee estimation is then performed by `calculate_weight_vbytes(tx_ins.len(), payments, None)` and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:204` and `send.rs:225-226`. Inside `calculate_weight_vbytes` (`send.rs:68-99`), the synthetic transaction's `output` vector is built **only** from `payments` plus an optional `change` output — there is no parameter for, and no inclusion of, the OP_RETURN data output. [2](#0-1) 

3. Consequences:
   - `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) and `fee_with_change = fee_per_vbyte * vbytes_with_change` (`send.rs:227`) understate the fee required to achieve the requested rate on the real transaction, which additionally carries an OP_RETURN output of up to ~93 weight units (80-byte payload plus output overhead).
   - The change amount `input_sat - payment_sat - fee_with_change` (`send.rs:228-230`) therefore returns slightly *more* change than intended — no funds are lost to change, but the effective fee rate of the final signed transaction is below `fee_per_vbyte`.
   - The `weight > MAX_STANDARD_TX_WEIGHT` check at `send.rs:241` uses `weight`/`weight_with_change`, both of which exclude the data output, so a transaction near the standardness limit can pass the check while the real transaction exceeds it. [3](#0-2) 

This is reachable via untrusted bytes: `data` is the transaction data payload attached to outbound payments (the OP_RETURN used to carry instructions/data on Bitcoin), capped at 80 bytes by the `TooMuchData` check at `send.rs:171-173`. The transaction constructed here is the one the FROST `sign` flow signs (`SignableTransaction::sign` uses these `tx`/`prevouts`), so the misaccounted output is baked into the signed artifact.

### Impact Explanation
- **Fee shortfall:** the signed transaction's effective fee rate is strictly less than `fee_per_vbyte` whenever `data` is `Some`. If the caller requests a rate at/near the minimum relay fee, the real transaction can fall below 1 sat/vB and be rejected by Bitcoin relay policy — the transaction will not propagate or confirm. Because the eventuality system waits on this exact `txid` (`Eventuality(signable.txid())` in `processor/src/networks/bitcoin.rs:830`), the plan stalls: no funds are stolen, but payments are delayed and the scheduler must re-plan, which is the "accounting produces a transaction inconsistent with its specification" analog of the Allo finding.
- **Standardness bypass:** the `MAX_STANDARD_TX_WEIGHT` guard can be satisfied by the modeled weight while the actual weight exceeds it, again producing a signed transaction Bitcoin nodes will not relay.
- Like the reference bug, the root cause is a mismatch between the value/output accounting used for checks and the outputs actually emitted — here the omission is structural (the estimator can't see the OP_RETURN output) rather than a missing refund.

Severity: Medium — liveness/DoS of specific plans (payments carrying data), no theft, recoverable by constructing a corrected transaction since the inputs remain unspent.

### Likelihood Explanation
The bug triggers on every `SignableTransaction` built with `data.is_some()`, since `data` is unconditionally excluded from both fee-estimation calls. Whether it causes an actual relay failure depends on how close the requested `fee_per_vbyte` is to the minimum relay rate and how much margin the scheduler leaves; the weight-limit bypass requires a near-maximal transaction plus a data output. Data-carrying transactions are a supported, documented path (`If data is specified, an OP_RETURN output will be added with it`, `send.rs:149`), so this is not an edge configuration.

### Recommendation
Include the data output in the fee model. Either:
- Extend `calculate_weight_vbytes` to take the `data` payload (or the full output list) and push the same `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) }` into the synthetic transaction, or
- Build the real `tx` first and compute `weight`/`vbytes` from `tx.weight()` directly, eliminating the duplicated model entirely (preferred — removes the class of drift between modeled and actual outputs).

Ensure both call sites (with and without change) account for the OP_RETURN output, and that the `MAX_STANDARD_TX_WEIGHT` check runs against the final transaction's real weight.

### Proof of Concept
```rust
// networks/bitcoin tests, regtest-style
// One confirmed ReceivedOutput `input` paying to `key`.
let addr = p2tr_script_buf(key).unwrap();
let payments = vec![(addr(), 1000)];

// Transaction WITHOUT data
let no_data = SignableTransaction::new(
    vec![input.clone()], &payments, None, None, /* fee_per_vbyte */ 5,
).unwrap();

// Transaction WITH an 80-byte OP_RETURN
let with_data = SignableTransaction::new(
    vec![input.clone()], &payments, None, Some(vec![0u8; 80]), 5,
).unwrap();

// Both report the same needed_fee, because calculate_weight_vbytes
// never sees the OP_RETURN output:
assert_eq!(no_data.needed_fee(), with_data.needed_fee());

// But the real transaction is larger:
assert!(with_data.tx.weight() > no_data.tx.weight());

// Effective fee rate of the signed data-carrying tx:
let actual_vbytes = u64::try_from(with_data.tx.vsize()).unwrap();
assert!(with_data.needed_fee() < 5 * actual_vbytes); // underpays requested rate
```

Caveat: I confirmed the omission statically — `calculate_weight_vbytes` has no `data` parameter and the OP_RETURN push at `send.rs:194-202` precedes both estimation calls. I did not execute the PoC against a regtest node, and I did not fully trace which processor call sites pass `Some(data)` into `SignableTransaction::new` (the `Payment.data` → `data` mapping in `make_signable_transaction` was not read end-to-end), though the parameter is documented and exercised in `networks/bitcoin/tests/wallet.rs:173` and `:186-190`.

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-202)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-243)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
