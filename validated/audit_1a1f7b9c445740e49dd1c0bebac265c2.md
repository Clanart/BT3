### Title
`SignableTransaction::new` omits the OP_RETURN output from fee/weight calculation, producing transactions that can fail to relay — an uncreatable-transfer path analogous to a missing `receive()` (File: networks/bitcoin/src/wallet/send.rs)

### Summary
In the Mellow report, a redemption flow was structurally incapable of completing: `SignatureRedeemQueue.redeem` always reverted for ETH because the queue contract could not receive value. The analogous Serai defect is a transaction-construction path that builds a spend transaction which cannot actually be published on-chain. `SignableTransaction::new` pushes an OP_RETURN output carrying `data` into the real transaction's output list, but computes `weight`, `vbytes`, `needed_fee`, and the change/decision math using only `payments` and `change` — the OP_RETURN output is never counted. The signed transaction is therefore larger than priced, its effective feerate is lower than requested, and at the supported low end of `fee_per_vbyte` it falls below Bitcoin's minimum relay fee, so the transaction cannot be broadcast and the funds cannot move — the transfer fails exactly like the ETH redemption revert.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before any fee accounting:

```rust
// networks/bitcoin/src/wallet/send.rs:193-202
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

But the size/fee computation that follows only ever sees `payments` and `change`:

```rust
// networks/bitcoin/src/wallet/send.rs:204-206
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

and inside `calculate_weight_vbytes`, the dummy transaction used to measure weight is built from `payments` plus an optional `change` output only — there is no parameter for `data`, so the OP_RETURN output (8-byte amount + length + up to 80-byte script, ~90 bytes ≈ ~91 vbytes of non-witness weight) is excluded. Both branches (with and without change) reuse this undercounted vbytes:

```rust
// networks/bitcoin/src/wallet/send.rs:225-232
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
let fee_with_change = fee_per_vbyte * vbytes_with_change;
```

The result is then committed into the real transaction (`tx_outs`, including the OP_RETURN) at `send.rs:245-255`, and the machine signs with `Prevouts::All` at `send.rs:373-390`, so the published byte size is irreducibly larger than the size the fee was computed for. Because the minimum-fee sanity check at `send.rs:211` is also evaluated against the undercounted `vbytes`, a transaction can pass `TooLowFee` checking while its true effective feerate is below `DEFAULT_MIN_RELAY_TX_FEE` (e.g., `fee_per_vbyte = 1` on a ~200-vbyte transaction paying for ~291 vbytes yields ~0.69 sat/vB). Such a transaction is non-standard, is rejected by `send_raw_transaction`, and hits the `panic!` path in `publish_completion` (processor-side caller), stalling the spend. Data-bearing spends (OutInstructions/burns attaching up to 80 bytes of attacker-influenced `data`, gated only by the `TooMuchData` check at `send.rs:171`) are the reachable trigger; the larger the `data`, the larger the feerate shortfall.

### Impact Explanation
Transactions created with a `data` payload systematically pay a lower effective feerate than the caller specified, and at low fee rates cannot be relayed or mined at all — the spend path is broken for that class of transaction, mirroring how ETH redemptions always reverted. Funds aren't stolen, but the multisig cannot publish the transaction it signed, blocking the transfer and (via the `panic!` on publish failure in the processor) faulting the signing pipeline rather than failing gracefully.

### Likelihood Explanation
Any caller passing `Some(data)` together with a `fee_per_vbyte` near the minimum creates a transaction that cannot be broadcast — no adversarial preconditions beyond supplying the data field, which external instructions legitimately control. At higher fee rates the tx still publishes but at a degraded effective feerate, so the miscalculation affects every data-bearing transaction unconditionally.

### Recommendation
Include the OP_RETURN output in `calculate_weight_vbytes` — e.g., extend the payment list or add a `data: Option<&[u8]>` parameter so the dummy transaction used for weight/vbytes measurement exactly mirrors `tx_outs` (payments + OP_RETURN + change). Alternatively, compute the fee from the fully-assembled transaction. Re-run the `TooLowFee` check against the true vsize.

### Proof of Concept
Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), 1)` on regtest: `tx.output` contains the OP_RETURN, but `needed_fee()` equals `1 * vbytes(payments+change-only)`. `tx.vsize()` exceeds the estimate by ~91 vbytes, so `fee() / tx.vsize() < 1 sat/vB`, and `send_raw_transaction` rejects it as below the minimum relay fee — the signed transaction cannot be published despite `new` having accepted the parameters.