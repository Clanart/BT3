### Title
`SignableTransaction::new` excludes the OP_RETURN output from the fee/weight simulation, understating the required fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Mantra `compute_offer_amount` issue — where a simulated "required input" is computed lower than what the actual execution path needs — `SignableTransaction::new` computes `needed_fee` and the max-weight check from a simulated transaction that omits the OP_RETURN `data` output. The signed transaction is therefore larger (and pays a lower effective fee rate) than the caller requested, and can even violate the standardness weight check the code claims to enforce.

### Finding Description
`SignableTransaction::new` builds `tx_outs` by first pushing the payment outputs and then the OP_RETURN output carrying `data` (up to 80 bytes, `send.rs:194-202`). However, the size simulation used for the fee and weight checks is called with `payments` only:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

and again for the change variant:

```rust
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

`calculate_weight_vbytes` reconstructs the transaction from `inputs`/`payments`/`change` — it never sees the OP_RETURN output, so both `weight` and `vbytes` exclude ~90+ real bytes (output base + script with up-to-80-byte push). Consequently:

- `needed_fee = fee_per_vbyte * vbytes` under-charges relative to the requested fee rate. When a change output exists, the change amount is `input_sat - payment_sat - fee_with_change`, so the final transaction pays exactly the understated fee — the extra OP_RETURN weight comes out of the effective feerate, never compensated.
- The minimum-relay check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (`send.rs:211`) also uses the understated `vbytes`, so a transaction whose *actual* feerate falls below min relay can be produced and signed.
- The standardness check `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241`) uses the understated `weight`, so a transaction exceeding the real max standard weight can pass. [1](#0-0) [2](#0-1) 

### Impact Explanation
Any caller that supplies `data` (an OP_RETURN payload is a documented feature of this API, and the processor uses OP_RETURN outputs for in-instructions) gets a transaction paying less than `fee_per_vbyte` per actual vbyte. At low requested feerates this yields transactions below the default minimum relay fee that will not propagate or confirm, or — near the weight limit — non-standard transactions that peers reject. Since the signature commits via `Prevouts::All` sighash over the *real* transaction, the signed fee cannot be corrected post-hoc; the input set is consumed and the output must be replaced/re-signed, burning time and potentially stranding funds in mempool-purgatory. This is the same class as the Mantra finding: a simulation returns a "sufficient" amount that execution proves insufficient.

### Likelihood Explanation
Reachable by an unprivileged party: the `data` parameter is arbitrary public input to `SignableTransaction::new`, and OP_RETURN payloads are used in Serai's deposit flow for in-instructions. The bug triggers deterministically whenever `data.is_some()`; no timing or adversarial ordering is needed. Impact is bounded (fee understatement of at most ~90 vbytes per transaction, or marginal standardness overflow), consistent with a Medium.

### Recommendation
Include the OP_RETURN output in the simulated transaction — either pass the constructed `tx_outs` into `calculate_weight_vbytes` instead of `payments`, or append a `TxOut` with `ScriptBuf::new_op_return(...)` to the simulation before measuring weight. Recompute `vbytes`/`needed_fee` after adding `data` and after adding `change`, so the min-relay and `MAX_STANDARD_TX_WEIGHT` checks reflect the true transaction.

### Proof of Concept
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

1. Call `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)` with `fee_per_vbyte` chosen so `fee_per_vbyte * vbytes` barely exceeds the min-relay threshold computed on the understated `vbytes`.
2. Observe `needed_fee` is computed from `calculate_weight_vbytes(tx_ins.len(), payments, change)` where `payments` lacks the OP_RETURN output.
3. After `multisig`/`preprocess`/`sign`/`complete` produce the `Transaction`, compute `tx.vsize()` — it exceeds `vbytes` by the OP_RETURN output's serialized size (~89+ vbytes for an 80-byte payload). The actual paid fee is `needed_fee`, so `fee_paid / tx.vsize() < fee_per_vbyte`, and for `fee_per_vbyte` at the relay floor the transaction is below `DEFAULT_MIN_RELAY_TX_FEE` on its true size and will not relay.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-212)
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
