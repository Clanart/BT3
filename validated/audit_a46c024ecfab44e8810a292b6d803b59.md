### Title
`SignableTransaction::new` computes weight/vbytes (and hence fee and change) without the OP_RETURN data output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external bug class is a value used at the wrong magnitude because a contributing term (token decimals) was never normalized into the computation. The Serai analog is in `SignableTransaction::new`: the transaction's weight and vbytes are derived from a reconstructed `Transaction` built only from `payments` and an optional change output, while an `OP_RETURN` output carrying up to 80 bytes of caller-supplied `data` is pushed onto `tx_outs` *after* the fee/change math. The data output's size is therefore never normalized into the fee, the change amount, or the max-weight check.

### Finding Description
`SignableTransaction::new` accepts `data: Option<Vec<u8>>` of up to 80 bytes and appends a zero-value `OP_RETURN` output to `tx_outs` (send.rs L194-202). However, both calls to `calculate_weight_vbytes` pass only `payments` and `change`:

- Initial fee: `(mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` then `needed_fee = fee_per_vbyte * vbytes` (L204-206).
- Change branch: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` and `fee_with_change = fee_per_vbyte * vbytes_with_change` (L225-228).
- Standardness check: `weight > MAX_STANDARD_TX_WEIGHT` (L241) uses the same data-less weight.

`calculate_weight_vbytes` itself faithfully builds a `Transaction` and calls `tx.weight()` (L68-101) — the omission is purely that the `OP_RETURN` output is never part of the inputs it is given. The missing contribution is the output base (8-byte value + compactsize script length) plus the `OP_RETURN` + push opcode + up to 80 data bytes, i.e. ~92+ weight units (~23+ vbytes) at max data size.

Two consequences follow:

1. **Underestimated fee / feerate.** `needed_fee` is short by `fee_per_vbyte * (data output vbytes)`. The `TooLowFee` check (L211) compares against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same understated `vbytes`, so a transaction that should have been flagged (or that falls below the node's real min-relay feerate once the `OP_RETURN` is serialized) is accepted. The signed transaction is consensus-valid (BIP-341 sighash commits to the real tx via `Prevouts::All`, L375-386) but pays a lower feerate than requested and can be non-relayable.
2. **Inflated change.** `change value = input_sat - (payment_sat + fee_with_change)` (L228-230) uses the underestimated `fee_with_change`, so the change output is credited the sats that should have covered the data output's fee share. The actual fee paid (`fee()`, L138-141, computed from real prevouts/outputs) is then even lower than `needed_fee` claims.

### Impact Explanation
Any spend that attaches `data` produces a transaction paying less than the requested `fee_per_vbyte`, and potentially below the relay minimum despite the `TooLowFee` guard passing. For a threshold wallet this means signed transactions that nodes refuse to relay or that sit unconfirmed, and change outputs carrying slightly more than intended (itself harmless, but it masks the fee shortfall). This is a genuine incorrect-formula bug reachable entirely through public inputs — `data`, `payments`, `change`, and `fee_per_vbyte` are all supplied by the transaction initiator — analogous to the oracle producing a price of the wrong magnitude because one term's scale was ignored.

### Likelihood Explanation
Triggered whenever `data` is `Some`. The protocol uses OP_RETURN data outputs in its Bitcoin flow, so this is not a corner case. Exploitation is limited: it degrades feerate/relayability rather than stealing funds, so Medium.

### Recommendation
Include the `OP_RETURN` output in the weight accounting — e.g. have `calculate_weight_vbytes` take the already-built `tx_outs` (including the data output), or pass `data`/`payments`-extended outputs into both call sites at send.rs L204 and L225-228 — so `needed_fee`, the change amount, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the true serialized transaction.

### Proof of Concept
```rust
// Conceptual: in SignableTransaction::new
let payments = vec![(some_script, 10_000u64)];
let data = Some(vec![0u8; 80]);

// vbytes is computed from a Transaction containing ONLY `payments`
let (_, vbytes) = calculate_weight_vbytes(1, &payments, None);
// but tx_outs later gains:
//   TxOut { value: 0, script_pubkey: OP_RETURN <80 bytes> }
// that output adds ~(8 + 1 + 1 + 1 + 80) bytes ≈ 91 wu ≈ 23 vbytes,
// none of which is in `vbytes`, so `needed_fee = fee_per_vbyte * vbytes`
// is short by ~23 * fee_per_vbyte sats and the TooLowFee check
// evaluates against a vbytes that is too small.
```
Root cause: `calculate_weight_vbytes(tx_ins.len(), payments, ...)` at `networks/bitcoin/src/wallet/send.rs` L204 and L226 omits the `OP_RETURN` output appended at L194-202. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-101)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
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

    let weight = tx.weight();
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
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
```
