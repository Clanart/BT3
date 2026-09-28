### Title
Fee and weight calculation omit the OP_RETURN data output, producing an under-priced and potentially non-standard transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds an OP_RETURN output carrying caller-supplied `data` to the transaction, but both calls to `calculate_weight_vbytes` estimate the transaction weight/vbytes using only the payment outputs (and optional change output). The data output's ~11–91 bytes are never included in the vbytes used to compute `needed_fee`, nor in the `weight` compared against `MAX_STANDARD_TX_WEIGHT`. The resulting transaction pays a lower effective feerate than requested and can exceed the standardness weight limit undetected.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `calculate_weight_vbytes` builds a mock transaction whose outputs are derived solely from `payments`, plus an optional change output: [1](#0-0) 

The OP_RETURN output is pushed into `tx_outs` before any weight estimation: [2](#0-1) 

but `calculate_weight_vbytes` is invoked with `payments` only — the `data` output is never passed in. The same omission applies to the change-path estimate (`vbytes_with_change`) at lines 224–234 and to the final standardness check: [3](#0-2) 

Concretely, an OP_RETURN output adds roughly `8 (value) + 1 (script len) + 1–2 (push opcode) + len(data)` base bytes, i.e., up to ~91 bytes (~364 WU, ~91 vbytes) for the maximum 80-byte payload. Two consequences follow:

1. **Under-estimated fee.** `needed_fee = fee_per_vbyte * vbytes` is computed on a vbytes figure up to ~91 vbytes too small. The actual signed transaction is larger, so its effective feerate is below `fee_per_vbyte`, and can fall below the mempool minimum relay feerate — the `TooLowFee` guard at lines 206–213 is validated against the underestimated size and cannot catch this. Such a transaction is valid consensus-wise but will not relay/confirm.
2. **Unenforced weight bound.** `weight > MAX_STANDARD_TX_WEIGHT` is checked against the underestimated weight. A transaction sized near the 400,000 WU limit can exceed it by up to ~364 WU, producing a non-standard transaction that Bitcoin nodes reject, even though `SignableTransaction::new` returned `Ok`.

This mirrors the reported bug class: the fee/share owed is computed against an incorrect denominator (the wrong transaction size), so the party constructing the transaction ends up with different economics than specified — the intended feerate is not the feerate paid, and the size bound that was supposed to be enforced is not.

### Impact Explanation
Any caller supplying `data` to `SignableTransaction::new` receives a transaction whose actual feerate is strictly lower than the `fee_per_vbyte` they specified and whose `needed_fee()`/`fee()` reporting does not reflect a fee sufficient for its real size. At low fee environments or near-boundary sizes, the produced transaction will not propagate or confirm, stalling the spend of the threshold key's funds; at the extreme it is outright rejected as non-standard. Because the discrepancy is deterministic given `data`, an unprivileged party able to influence the data/payment shape of a transaction the wallet constructs (e.g., payment counts/values and the OP_RETURN payload) can push a transaction over the relay threshold or weight limit without triggering the built-in guards.

### Likelihood Explanation
The flaw triggers whenever `data` is `Some`; the magnitude scales with `data` length (up to 80 bytes) and is most consequential for transactions already near the weight cap or built at the minimum relay feerate. It requires no malicious validator, no collusion, and no leaked key material — only public transaction-construction inputs.

### Recommendation
Include the data output in `calculate_weight_vbytes`: pass the constructed `tx_outs` (or the serialized OP_RETURN `ScriptBuf`) into the estimator so `weight`, `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check all account for the full final output set. Equivalently, build the mock transaction from the same `tx_outs` vector that will be placed on the real transaction, guaranteeing the estimate can never diverge from what is signed. Add a test asserting `needed_fee() == actual_vsize * fee_per_vbyte` and the correct error when `data` pushes the transaction over the standardness weight limit.

### Proof of Concept
Construct a `SignableTransaction` with a non-empty `data` payload and compare:

```rust
let payments = vec![(p2tr_script_buf(key).unwrap(), 1_000)];
let data = Some(vec![0u8; 80]);
let tx = SignableTransaction::new(inputs, &payments, None, data, FEE).unwrap();
// tx.needed_fee() == FEE * vbytes(payments-only-tx)
// actual tx serialized size is larger by the OP_RETURN output (~91 bytes),
// so tx.fee() / tx.transaction().vsize() < FEE
```

The equality `needed_fee == vsize * FEE` asserted in `networks/bitcoin/tests/wallet.rs` (line 269) holds only because that test passes `data: None`; with `Some(data)` the invariant fails, demonstrating the underestimated fee and weight.

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-204)
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

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
