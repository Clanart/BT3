### Title
`SignableTransaction::new` omits the OP_RETURN output from the fee/weight estimate, silently underpaying the fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The external report is a fee-accounting bug: `getPositionFees` sums a fee component (borrowing fee for the receiver) twice into `totalNetCostAmount`, so the user is charged more than intended. The analog in Serai's in-scope `bitcoin-serai` wallet is the mirror image of the same class — inconsistent fee accounting where a transaction component is omitted from the fee computation instead of double-counted. `SignableTransaction::new` appends an `OP_RETURN` output to the transaction (send.rs:194-202) but computes `needed_fee` via `calculate_weight_vbytes(tx_ins.len(), payments, None)` (send.rs:204), which only models the `payments` outputs and never the data output. The signed transaction is therefore larger than the size the fee was priced for.

### Finding Description
In `SignableTransaction::new`:

1. If `data` is provided, an `OP_RETURN` `TxOut` (up to ~83 bytes: 1-byte opcode, pushdata header, ≤80 bytes payload, 8-byte value, compactsize length) is pushed into `tx_outs` (lines 194-202).
2. The weight/vbyte estimate at line 204 calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, and that helper builds its dummy transaction exclusively from `payments` (lines 85-98) — the `data` output is absent.
3. `needed_fee = fee_per_vbyte * vbytes` (line 206) and the minimum-relay check (line 211) are both evaluated against this underestimated `vbytes`.
4. The change calculation repeats the same omission: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226 also excludes the OP_RETURN output, so `fee_with_change` is underestimated as well.
5. The final `Transaction` (lines 245-251) does include the OP_RETURN output, so the transaction signed via `TransactionSignMachine::sign` (lines 383-390, `taproot_key_spend_signature_hash` over `Prevouts::All`) commits to a larger transaction than was priced.

The actual fee paid is `sum(inputs) - sum(outputs) = needed_fee` (fee(), lines 138-141), but the real vsize is larger than `vbytes`, so the effective fee rate is strictly below `fee_per_vbyte`. When `data` is non-trivial (~83 extra vbytes ≈ a large relative increase for a small transaction), the effective rate can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the check at line 211 passed, producing a signed transaction that Bitcoin nodes will reject as non-standard/underpriced. The change branch makes this worse: the change amount `input_sat - payment_sat - fee_with_change` is also computed off the wrong fee, so the change output can be over- or under-funded relative to the intended rate.

### Impact Explanation
An unprivileged party who can cause a transaction to carry `data` (an OP_RETURN payload in a payment/instruction routed through `SignableTransaction::new`) causes the multisig to produce a transaction whose actual fee rate is lower than the rate requested — and potentially below the minimum relay fee — without any error being raised. The result is a signed transaction that either cannot be broadcast (stuck/unspendable plan outputs until reconstructed) or confirms far more slowly than priced, while `needed_fee()` and the change amount report incorrect, internally inconsistent fee accounting. This is the same class as the report — fee components accounted inconsistently — realized as an undercharge rather than an overcharge.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is called with `Some(data)` and `fee_per_vbyte` is low enough that ~83 unpriced vbytes drop the effective rate under the minimum, or whenever a caller relies on `needed_fee()` for budgeting. It requires no key material, no threshold collusion, and no malicious validator — only caller-supplied transaction data. Severity is bounded (Medium): funds are not stolen, but transactions are mispriced and can be non-relayable.

### Recommendation
Include the OP_RETURN output in the weight estimate. Either build the `tx_outs` vector before calling `calculate_weight_vbytes` and pass the full output list (payments + OP_RETURN + optional change), or change `calculate_weight_vbytes` to accept the actual `Vec<TxOut>` used in the final transaction so the priced size always matches the signed size. Add a regression test asserting `signable.fee() / actual_vsize >= fee_per_vbyte` when `data` is set.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs behavior
// SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), 1)
//
// Line 194-202: tx_outs = payments + OP_RETURN(80 bytes)   // real TX has N+1 outputs
// Line 204:     calculate_weight_vbytes(n, payments, None) // priced TX has N outputs
// Line 206:     needed_fee = 1 * vbytes                    // missing ~83+ vbytes
//
// The produced Transaction (lines 245-251) contains the OP_RETURN output,
// so actual_vsize ≈ vbytes + 83 while actual fee = needed_fee.
// Effective rate = needed_fee / actual_vsize < 1 sat/vB
//   -> below DEFAULT_MIN_RELAY_TX_FEE -> rejected by relay policy,
// despite the check at line 211 passing.
```

Relevant code: `SignableTransaction::new` OP_RETURN insertion and unpriced weight estimate ( [1](#0-0) ), `calculate_weight_vbytes` building the dummy tx only from `payments` ( [2](#0-1) ), change branch repeating the omission ( [3](#0-2) ), and `fee()` showing actual fee equals `inputs - outputs` ( [4](#0-3) ).

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

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

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
