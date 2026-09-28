The in-scope code is `crypto/*` and `networks/bitcoin/src` only — processor findings are out of scope. Let me look at the Bitcoin wallet transaction construction for dropped/ignored data (the analog of "returns success without handling input").### Title
`SignableTransaction::new` omits the OP_RETURN data output from fee/weight estimation, producing underpriced transactions — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
CVE-2024-50168 is a "resource silently dropped / work not accounted for" bug: `sun3_82586_send_packet()` returned success for an oversized skb without freeing it. The Serai analog lives in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`: when `data` is supplied, an `OP_RETURN` output is appended to the real transaction's output list (`tx_outs`), but all weight/vsize/fee calculations are performed on `payments` alone — the OP_RETURN output is silently excluded from the accounted size. The transaction is still constructed and signed "successfully," yet its effective fee rate is lower than the caller-specified `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE`.

### Finding Description
In `SignableTransaction::new`, outputs are built into `tx_outs`, and when `data: Option<Vec<u8>>` is `Some`, an `OP_RETURN` output carrying up to 80 bytes is pushed:

```rust
// networks/bitcoin/src/wallet/send.rs
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

Both the initial estimate and the change-aware estimate call `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which reconstructs a dummy transaction whose outputs are built **only from `payments`**:

```rust
output: payments
  .iter()
  .map(|payment| TxOut { ... })
  .collect(),
```

The OP_RETURN output (~8-byte value + script with up to ~83 bytes of pushed data, i.e. up to roughly 90 extra vbytes) is never included in `weight`/`vbytes`. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` is computed for a smaller transaction than the one actually produced and signed.
- The `TooLowFee` guard (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against the underestimated `vbytes`, so a transaction whose *actual* fee rate is below the mempool minimum-relay fee can pass the check.
- `needed_fee()` publicly reports a wrong value, and the `MAX_STANDARD_TX_WEIGHT` check likewise ignores the data output.

Any party that causes a transaction to be built with `data` (e.g., an `InInstruction`-bearing OP_RETURN, which is the documented mechanism for deposit instructions on Bitcoin — "Bitcoin In Instructions are present via the transaction's last output in the form of OP_RETURN") reaches this path; `data` up to 80 bytes is accepted by the `TooMuchData` check.

### Impact Explanation
The signed transaction pays `needed_fee` sats but is larger than estimated, so its real fee rate is strictly below `fee_per_vbyte`. In the worst case the actual fee rate drops below `DEFAULT_MIN_RELAY_TX_FEE`, making the completed FROST-signed transaction non-standard and rejected by Bitcoin Core mempool relay — a completed signing ceremony yields a transaction that cannot propagate. Even when it relays, the TX confirms at a lower effective feerate than policy requested, and `needed_fee()` misreports to callers doing fee accounting. This is a concrete incorrect-fee formula reachable purely via caller-supplied `data` bytes.

### Likelihood Explanation
Triggered deterministically whenever `SignableTransaction::new` is called with `Some(data)` — no attacker privilege is needed beyond supplying the bytes that end up in `data` (the normal OP_RETURN instruction channel). The underpayment magnitude is bounded (~90 vbytes), so mempool rejection additionally requires `fee_per_vbyte` to be near the minimum relay fee; the feerate under-delivery itself occurs on every such call.

### Recommendation
Pass `tx_outs` (or an equivalent representation including the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments`, in both the no-change and with-change invocations, so `weight`, `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction that will actually be signed. Alternatively, compute the fee after fully constructing `tx_outs` and then append change.

### Proof of Concept
1. Construct one `ReceivedOutput` worth `input_sat` and one payment of `payment_sat`.
2. Call `SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` is set so `needed_fee` just exceeds `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` for the underestimated `vbytes`.
3. Observe the returned transaction contains an OP_RETURN output (~90 vbytes) not counted in `needed_fee`; the signed transaction's real feerate is `needed_fee / actual_vbytes < fee_per_vbyte`, and for marginal `fee_per_vbyte` it falls under `DEFAULT_MIN_RELAY_TX_FEE`, making the transaction non-standard despite the constructor returning `Ok`.