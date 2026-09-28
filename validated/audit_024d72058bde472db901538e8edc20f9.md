### Title
OP_RETURN output excluded from weight/fee accounting produces transactions too large or under-paid to relay - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to [H-04] — an L2 precompile call that succeeds on L2 but can never be reproduced on L1 because its true resource cost exceeds what the reproduction path can supply — `SignableTransaction::new` builds a transaction whose fee and standardness checks are computed over a *smaller* transaction than the one actually signed. The OP_RETURN output is appended to `tx_outs` but the `calculate_weight_vbytes` helper only measures `payments` and `change`, so the data output's weight is invisible to both the fee calculation and the `MAX_STANDARD_TX_WEIGHT` check. The result is a transaction the multisig will happily sign that the Bitcoin network will refuse to relay — an action valid at signing time that is impossible to execute on-chain.

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` at send.rs:194-202:

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

However, both weight measurements — the base call at send.rs:204 and the with-change call at send.rs:225-226 — pass `payments` (the caller's payment list), which never contains the OP_RETURN output:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (send.rs:62-127) reconstructs the transaction's `output` vector solely from `payments` plus an optional `change`, so every output it weighs is one the caller listed — the OP_RETURN is absent. Two consequences:

1. **Under-estimated vbytes → under-paid fee.** `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) omits roughly `(8 + 1 + ~2..82)` serialized bytes × 4 weight units, i.e. up to ~90 vbytes for a max-size 80-byte payload (the `data.len() > 80` check at send.rs:171 caps this). The actual fee paid, `input_sat − output_sum` (send.rs:139-141), equals the under-computed `needed_fee`. When `fee_per_vbyte` is at or near the minimum relay rate, the transaction's *effective* feerate falls below `DEFAULT_MIN_RELAY_TX_FEE` — the only minimum-fee validation performed (send.rs:211) uses the understated `vbytes`, so it passes even though the real transaction would fail it.

2. **Unenforced standardness bound.** `weight > MAX_STANDARD_TX_WEIGHT` is checked at send.rs:241 against the same understated `weight`. A transaction already near the 400,000 WU policy limit that also carries an OP_RETURN output can pass this check while its true weight exceeds the limit, making it non-standard and unrelayable by any default-policy node.

The inputs (`ReceivedOutput`s) and the OP_RETURN `data` are derived from externally caused deposit data and user-requested burn metadata, so an unprivileged party can influence both the input count and the payload size. Unlike H-04's gas accounting, there is no second chance here: `Prevouts::All` (send.rs:375) commits every input to the sighash, so once `multisig()`/`sign()`/`complete()` run, the signatures bind to the oversized/underfunded transaction and cannot be repurposed for a corrected fee.

### Impact Explanation
A multisig-signed Bitcoin transaction that no relay node will accept: either its effective feerate is below the minimum relay fee, or its true weight exceeds `MAX_STANDARD_TX_WEIGHT`. Funds are locked in the inputs of a transaction that can never confirm; the inputs are already committed via `Prevouts::All`, so recovering them requires an entirely new signing round, and the failed transaction's nonces/preprocesses are consumed. In the worst case the coordinator reports a payout as sent that can never arrive — the Bitcoin analog of an L2 action whose L1 reproduction is impossible. Severity: Medium (impact is temporary fund lockup / liveness of payouts, requiring a large-input transaction or minimum-fee signing; no direct theft).

### Likelihood Explanation
Two reachable paths:

- Any burn/withdrawal that attaches OP_RETURN metadata while signing at the minimum feerate produces a transaction whose real feerate is below relay minimum — reachable by any user who can attach up to 80 bytes of data.
- A transaction aggregating many deposited `ReceivedOutput`s (each ~58 vbytes; an attacker can create arbitrarily many small deposits to the scanned address via `Scanner::scan_transaction`, which accepts any matched `script_pubkey` at wallet/mod.rs:205) can approach the weight limit, at which point the uncounted OP_RETURN output pushes the true weight over `MAX_STANDARD_TX_WEIGHT` while send.rs:241 still passes.

### Recommendation
Include the OP_RETURN output in weight/fee accounting. Pass the fully constructed `tx_outs` (or `payments` plus the data output) into `calculate_weight_vbytes` so `vbytes`/`weight` reflect the final signed transaction; alternatively, add a `data: Option<&[u8]>` parameter to `calculate_weight_vbytes`. Additionally, after constructing the final `Transaction` in `SignableTransaction::new`, check `tx.weight()` directly against `MAX_STANDARD_TX_WEIGHT` rather than the estimate.

### Proof of Concept
Conceptual trace, no separate harness needed:

1. `Scanner` registers the multisig's tweaked P2TR script (wallet/mod.rs:162-196). An external party deposits outputs.
2. Coordinator calls `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` equals the minimum relay rate converted to sats/vbyte.
3. At send.rs:194-202 a ~91-byte OP_RETURN `TxOut` is pushed to `tx_outs`; at send.rs:204 `calculate_weight_vbytes` is called with `payments`, omitting it.
4. `needed_fee = fee_per_vbyte * vbytes` and the `TooLowFee` check at send.rs:211 both use `vbytes` missing ~90 vbytes; both pass.
5. `multisig()` → `preprocess()` → `sign()` (send.rs:355-398) signs each input with `Prevouts::All` — binding signatures to the full `tx` including the OP_RETURN output.
6. The completed transaction's serialized size is ~90 vbytes larger than the fee covers; its real feerate `< DEFAULT_MIN_RELAY_TX_FEE` (or, near the cap, `tx.weight() > MAX_STANDARD_TX_WEIGHT`). Bitcoin Core rejects it with `min relay fee not met` / `tx-size` — the signed artifact is impossible to broadcast, exactly mirroring H-04's irreproducible L1 step.