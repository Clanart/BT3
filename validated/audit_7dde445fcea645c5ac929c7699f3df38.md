### Title
OP_RETURN data output is excluded from the fee/weight calculation, so transactions with data underpay the fee rate and can exceed the standard weight limit - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an `OP_RETURN` output onto `tx_outs` when `data` is supplied, but both calls to `calculate_weight_vbytes` are made with `payments` — not `tx_outs` — so the extra output's size is never included in the vbyte estimate. The result is that `needed_fee` is computed for a smaller transaction than the one actually constructed. This mirrors the D3Trading bug where `swapFee` was computed but never applied to the returned amount: here a required component of the fee basis is silently omitted from the amount carved out as fee/change.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs:194-202`, the OP_RETURN output is appended to `tx_outs`. However, the weight/vbyte estimate at line 204 and the with-change estimate at line 226 pass `payments` to `calculate_weight_vbytes`, which builds a mock transaction containing only payment outputs (plus optional change). The data output — up to 80 bytes of pushdata plus length/amount overhead — is absent from that mock.

Consequences:

1. **With change**: `change_value = input_sat - payment_sat - fee_with_change` (lines 228-230). The actual fee paid equals `fee_with_change`, but the real transaction is larger than `vbytes_with_change` by the OP_RETURN output size, so the effective fee rate is strictly below the caller-specified `fee_per_vbyte`. If `fee_per_vbyte` is near the relay minimum, the real rate can drop under `DEFAULT_MIN_RELAY_TX_FEE`, making the transaction unbroadcastable — yet `SignableTransaction::new` still returns `Ok` and `needed_fee()`/`fee()` report a "satisfactory" fee.
2. **Weight bound**: the `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the underestimated `weight`. A transaction near the limit with a data output will actually exceed 400,000 WU and be rejected as nonstandard by every relaying node.
3. **`NotEnoughFunds` check** at line 215 compares against the under-estimated `needed_fee`, so a marginal input set is accepted where the true required fee would not be covered at the requested rate.

Unlike the no-change case (where all leftover becomes fee, so the tx merely overpays and stays above the rate), the change path deterministically underpays relative to the requested rate because the change absorbs exactly `fee_with_change` for a larger actual transaction.

### Impact Explanation
An unprivileged caller supplying `data` (a documented parameter of `SignableTransaction::new`) together with a change address obtains a signed-ready transaction that pays a lower fee rate than requested and promised by `needed_fee()`, potentially below the minimum relay fee — leaving funds (inputs + change) locked in an unconfirmable/unbroadcastable transaction. Alternatively, near the weight cap the produced transaction is outright nonstandard and rejected on broadcast. This is "funds reported sent that are not spendable/confirmable", reachable purely through public inputs.

### Likelihood Explanation
Any caller who uses both `data` and `change` triggers it; `data` may be up to 80 bytes, shifting the real vsize by ~85+ bytes. The miss is deterministic, not edge-case timing. Severity Medium: no funds are stolen and the tx remains valid consensus-wise, but the fee-rate guarantee the API promises is broken and transactions can stall or fail to relay.

### Recommendation
Include the data output in the weight estimate: pass `tx_outs` (or payments + a synthetic OP_RETURN output) to `calculate_weight_vbytes` for both the base and with-change calculations, so `needed_fee`, `fee()`, the `NotEnoughFunds` check, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the actual transaction.

### Proof of Concept
- Inputs: one `ReceivedOutput` of value V; `payments = [(script, P)]`; `data = Some(vec![0; 80])`; `change = Some(change_script)`; `fee_per_vbyte = r` chosen so `r * vbytes` just clears `DEFAULT_MIN_RELAY_TX_FEE`.
- `SignableTransaction::new` returns `Ok` with `needed_fee = r * vbytes_with_change`, and pushes change of `V - P - needed_fee`.
- The built `tx` has 3 outputs (payment, OP_RETURN, change). Its real vsize is `vbytes_with_change + ~83` (8-byte amount + ~82-byte OP_RETURN scriptPubKey).
- `tx.fee() == needed_fee`, so the real feerate is `needed_fee / real_vsize < r`, and can fall below 1 sat/vbyte — nodes reject it from their mempool despite `TooLowFee` having passed.
- Weight variant: choose inputs/payments so `vbytes_with_change` is just under the standard weight; the real tx exceeds `MAX_STANDARD_TX_WEIGHT` while the check at line 241 passes. [1](#0-0) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-243)
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
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
