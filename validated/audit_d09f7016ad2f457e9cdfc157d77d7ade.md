### Title
Single hardcoded `DUST` constant rejects valid outputs and burns spendable change — dust is script-dependent, not universal - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` applies one hardcoded dust threshold, `pub const DUST: u64 = 546`, to every payment output and to the change-output decision, regardless of the output's `script_pubkey` type and size. In Bitcoin, the dust threshold is not a universal constant — Bitcoin Core computes it per output as `3 * minRelayFee * (serialized output size + spend size)`, which is ~330 sats for a P2TR/P2WPKH output and 546 sats only for legacy P2PKH outputs. Applying the P2PKH value to all outputs mirrors the audited Moonwell bug class: one constant (`initialMintAmount = 1 ether`) silently reused across domains (WETH vs. USDC decimals; here, P2PKH vs. SegWit/Taproot script sizes) where it is wrong for one of them, causing construction of an otherwise-valid operation to fail.

### Finding Description [1](#0-0) 

In `SignableTransaction::new`, every caller-supplied payment `(_, amount)` is rejected if `*amount < DUST`: [2](#0-1) 

The payments are `(ScriptBuf, u64)` pairs whose script determines the true dust threshold; P2TR outputs (`ScriptBuf::new_p2tr_tweaked`, used for Serai's own keys via `p2tr_script_buf`) have a Core dust threshold of ~330 sats, P2WPKH/P2WSH ~294–330 sats. Any payment to a SegWit output in the range `[330, 546)` is valid, relayable, and spendable, yet `new` returns `TransactionError::DustPayment` and refuses to build the transaction.

Symmetrically, when deciding whether leftover funds become a change output, the same `546` gate is used: [3](#0-2) 

Serai change outputs are P2TR (true dust ~330 sats). Leftover change in `[330, 546)` is silently absorbed into the miner fee instead of being returned: `input_sat - payment_sat - fee_with_change` is simply not emitted, and `fee()` (`sum(inputs) - sum(outputs)`) then reports an inflated fee the signer never intended. The comment above the constant even acknowledges lower SegWit thresholds exist but declines to differentiate: [4](#0-3) 

### Impact Explanation
Two concrete consequences reachable with public inputs (the `payments` and `change` arguments to `SignableTransaction::new`):

1. **Liveness/failure analog to the original report**: constructing a transaction that pays e.g. 400 sats to a P2TR address always errors with `DustPayment`, even though the transaction would be standard, relayed, and mined. The send path deterministically fails for a class of valid payments — the same shape as a deploy that deterministically fails because one constant was wrong for one market.
2. **Unintended fee burn**: when change falls in `[330, 546)`, the change output is dropped and the difference is paid as fee, without any error or indication. `SignableTransaction::fee()` then returns a value larger than the `needed_fee` the transaction was built around, misreporting the intended economics of the spend.

### Likelihood Explanation
The `payments` slice is attacker/user-controlled data — withdrawal destinations and amounts originate externally. Any request in the affected band hits the failure deterministically (100% for that input), though it only affects payments in a narrow ~216-sat window and requires callers to actually construct such amounts. Change landing in the window is likewise value-dependent. No secret material is involved and no forged signatures result, which bounds severity to Medium.

### Recommendation
Per the original report's fix pattern ("specify `initialMintAmount` for every token separately"), compute the dust threshold per output from its serialized size instead of using a single constant — i.e., implement `dust_threshold(script_pubkey)` ≈ `3 * minRelayFeePerKvb/1000 * (GetSerializeSize(txout) + spend_input_size)`, or at minimum use the correct lower bound for SegWit outputs (330 sats for P2TR change) and keep `546` only where the script is unknown/legacy-sized.

### Proof of Concept
`SignableTransaction::new` is public; all arguments are caller-supplied:

```rust
// A 400-sat payment to a standard P2TR output. Dust for this output is ~330 sats,
// so this transaction is valid and relayable under Bitcoin Core policy.
let payment_script = p2tr_script_buf(some_even_key).unwrap();
let payments = [(payment_script, 400u64)];

// Deterministically fails — the single hardcoded 546-sat constant was derived
// for legacy P2PKH outputs and is wrong for SegWit outputs.
let err = SignableTransaction::new(inputs, &payments, None, None, fee_per_vbyte);
assert_eq!(err.unwrap_err(), TransactionError::DustPayment);
```

For the change-burn case: choose `inputs`, `payments`, `fee_per_vbyte`, and a P2TR `change` script such that `input_sat - payment_sat - fee_with_change ∈ [330, 546)`. `new` succeeds, emits no change output, and `tx.fee()` exceeds `tx.needed_fee()` by that amount — funds intended for the change address are paid to miners instead.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L28-32)
```rust
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L228-234)
```rust
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```
