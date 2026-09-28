### Title
`SignableTransaction::new` computes transaction weight and required fee without the OP_RETURN data output, underpaying the fee and bypassing the standard-weight limit - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the external report (a quantity normalized against the wrong denominator, so the "rate" is computed on a value smaller than reality), `SignableTransaction::new` calculates the transaction weight/vbytes — and therefore `needed_fee`, the minimum-relay-fee check, and the `MAX_STANDARD_TX_WEIGHT` check — using only `payments` and `change`, while an attacker-controlled OP_RETURN output of up to 80 bytes of data is pushed into `tx_outs` but never included in the weight calculation. The signed transaction is larger and pays less sat/vbyte than requested.

### Finding Description
In `SignableTransaction::new`, the size and fee are computed from the payment list only: [1](#0-0) 

`tx_outs` gets an additional OP_RETURN output carrying up to 80 bytes of caller-supplied `data` (checked at lines 171-173 against `TooMuchData`), but `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` and later `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` are called with `payments`, not `tx_outs` — so the data output's ~90+ bytes (≈368 WU ≈ 92 vbytes) are excluded from `weight`, `vbytes`, `vbytes_with_change`, `needed_fee`, and `fee_with_change`: [2](#0-1) 

Consequences of the mis-scaled denominator:
- `needed_fee = fee_per_vbyte * vbytes` underprices the real virtual size; the actual fee paid is `inputs - outputs`, so the leftover intended for change is silently consumed as fee instead — the LP/LP-equivalent here (the multisig's change output) receives less than computed, or the change output is dropped entirely when the true fee pushes the remainder below `DUST`.
- The `TooLowFee` check at lines 211-213 validates the min-relay rate against the smaller vsize, so a transaction paying below `DEFAULT_MIN_RELAY_TX_FEE` on its real vsize can pass.
- The `MAX_STANDARD_TX_WEIGHT` check at lines 241-243 can pass a transaction whose real weight exceeds the standardness limit by up to ~368 WU, producing a signed transaction that Bitcoin nodes refuse to relay.

### Impact Explanation
Like the vault report, the computed "rate" (sat/vbyte) is applied to a quantity that fails to account for all units being paid for. The wallet can emit a transaction that (a) pays a lower effective feerate than the caller requested and than the relay check attested, and (b) can exceed `MAX_STANDARD_TX_WEIGHT`, making the FROST-signed transaction unrelayable. Funds committed to that transaction cannot move until a corrected transaction is re-signed; the accounting error is permanent for any consumer relying on `needed_fee()`/`fee()` semantics.

### Likelihood Explanation
Reachable with public inputs: `SignableTransaction::new` is called by the scheduler with payment plans whose `data` field derives from user-submitted `InInstruction` payloads on Bitcoin. Any user who causes a withdrawal/batch with a nonzero `data` payload triggers the underpayment; a payload near the 80-byte cap maximizes the discrepancy. No collusion or privileged position is required.

### Recommendation
Compute weight/vbytes over the actual output set — pass the fully-constructed `tx_outs` (payments + OP_RETURN + change candidate) to `calculate_weight_vbytes`, or add the serialized OP_RETURN output's size into the calculation, so `needed_fee`, the min-relay check, the change-output dust check, and the weight check all reflect the transaction that will actually be signed in `TransactionSignMachine::sign`.

### Proof of Concept
```rust
// networks/bitcoin wallet (std feature)
let data = vec![0xaa; 80];
let tx = SignableTransaction::new(
    vec![funded_output],            // ReceivedOutput with enough value
    &[(payment_script, 1000)],
    None,
    Some(data),                     // OP_RETURN output added to tx_outs
    10,                             // fee_per_vbyte
).unwrap();

// needed_fee used 10 * vbytes(without the OP_RETURN output)
// The real transaction is ~92 vbytes larger:
assert!(tx.transaction().vsize() as u64 * 10 > tx.needed_fee());
// Real feerate < requested feerate; a weight near the limit can exceed
// MAX_STANDARD_TX_WEIGHT undetected because `weight` excluded the data output.
```

Note: the OP_RETURN output carries `value: Amount::ZERO` (send.rs:195-201), so only its size — not its value — is missing from the accounting; the bug is purely the denominator mis-scaling, matching the reported bug class.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-235)
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
