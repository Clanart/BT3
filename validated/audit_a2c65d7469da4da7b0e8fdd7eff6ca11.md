### Title
`SignableTransaction::new` computes fee and weight without the OP_RETURN output, underpaying the fee and inflating change - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a payout computed against a mutated/decremented amount rather than the amount that will actually be used, leaving a residual that breaks later payouts. `SignableTransaction::new` exhibits the same class: it pushes an `OP_RETURN` output into `tx_outs`, but then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which reconstructs the weight from `payments` only — never including the `data` output. The fee target (`needed_fee`), the change amount, and the max-weight check are all computed on a transaction smaller than the one actually signed and broadcast.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is appended to `tx_outs` when `data` is supplied:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...)
  })
}
``` [1](#0-0) 

However, both calls to `calculate_weight_vbytes` pass `payments` — which does not contain the `OP_RETURN` output — so the reconstructed transaction used for `tx.weight()` omits it:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
``` [2](#0-1) [3](#0-2) 

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` is computed over a smaller vsize than the real transaction, so the change output is set to `input_sat - payment_sat - fee_with_change` where `fee_with_change` is too small. The resulting signed transaction carries a real fee of exactly `fee_with_change` spread over more vbytes — i.e. an effective fee rate strictly below `fee_per_vbyte`.
2. The minimum-relay-fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` uses the same underestimated `vbytes`, so a `fee_per_vbyte` near the relay minimum can pass this check while the actual transaction is below the minimum relay fee and will not be relayed/confirmed — the funds it spends are effectively frozen despite the transaction being signed correctly.
3. The `weight > MAX_STANDARD_TX_WEIGHT` check uses `weight`/`weight_with_change` that exclude the OP_RETURN output (~10–90 extra weight units for up to 80 bytes of data), so a transaction at the boundary can be constructed and signed yet be non-standard and rejected by relay.

### Impact Explanation
`needed_fee()` is documented as the fee required to achieve the requested rate, and callers/integrators (the signature machinery signs whatever `self.tx` contains) rely on it. With `data` set, every signed transaction silently pays a lower effective fee rate than requested; at low fee rates this produces a validly-signed but non-relayable transaction, permanently locking the spent inputs until an out-of-band remedy (e.g. fee bump is impossible for SIGHASH_DEFAULT-key-spend without new signatures). This is the same failure mode as the RFPSimpleStrategy report: an amount derived from a stale/reduced base leaves the actual obligation (here, the fee proportional to real vsize) underfunded.

### Likelihood Explanation
The trigger requires `data.is_some()`. Serai's own processor call site passes `None` (`processor/src/networks/bitcoin.rs`), which limits exposure for in-protocol burns; however `SignableTransaction::new` is public API of the `bitcoin-serai` wallet library and the `data` parameter exists precisely for callers embedding `OP_RETURN` payloads (as exercised in `networks/bitcoin/tests/wallet.rs`). Any such caller hits the under-estimation deterministically; the severity of the outcome scales with how close `fee_per_vbyte` is to the relay minimum.

### Recommendation
Pass the actual output set (including the `OP_RETURN` output) into `calculate_weight_vbytes` — e.g. change it to accept `&[TxOut]` and pass `&tx_outs` — for both the no-change and with-change estimations, so `needed_fee`, the change amount, and the weight check reflect the transaction that is actually signed.

### Proof of Concept
```rust
// networks/bitcoin SignableTransaction::new(inputs, payments, change, Some(data), fee_per_vbyte)
// With fee_per_vbyte = F and an OP_RETURN output of V_extra vbytes:
//   needed_fee recorded = F * vbytes_without_opreturn
//   actual tx vsize     = vbytes_without_opreturn + V_extra
//   actual fee paid     = inputs - outputs = needed_fee
//   effective rate      = needed_fee / (vbytes + V_extra) < F
// If F is at/near DEFAULT_MIN_RELAY_TX_FEE, effective rate < relay minimum
// -> transaction is signed but rejected from mempools.
// Additionally, weight check omits the OP_RETURN bytes, so a tx with
// weight in (MAX_STANDARD_TX_WEIGHT - w_opret, MAX_STANDARD_TX_WEIGHT]
// is constructed despite being non-standard.
```
The fix-verifying assertion is `assert_eq!(tx.vsize() as u64 * fee_per_vbyte, signable.needed_fee())` which currently fails whenever `data.is_some()`.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
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
