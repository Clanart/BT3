### Title
Integer overflow in `SignableTransaction::new` balance check allows bypassing `NotEnoughFunds` validation with untrusted payment amounts - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The CVE-2016-2538 class — integer overflows when handling attacker-influenced values — has an analog in the Bitcoin wallet's transaction construction. `SignableTransaction::new` sums untrusted payment amounts and adds the fee with unchecked `u64` arithmetic (`payment_sat + needed_fee`, `input_sat` via `sum::<u64>()`). With a crafted payment amount near `u64::MAX` (reachable via InInstruction payment amounts that flow into `payments` in `make_signable_transaction` / `prepare_send`), `payment_sat + needed_fee` wraps to a small value in release builds, defeating the funds-sufficiency check. The change computation `input_sat.checked_sub(payment_sat + fee_with_change)` also wraps the same way, producing a small `Some(value)` that may pass the `>= DUST` test and push a change output. A `SignableTransaction` is then returned whose outputs vastly exceed its inputs — a transaction the validation explicitly intended to reject.

### Finding Description
`networks/bitcoin/src/wallet/send.rs:187` computes `payment_sat` as an unchecked `sum::<u64>()` over caller-supplied payment amounts. `send.rs:215` checks `input_sat < (payment_sat + needed_fee)` — the addition can overflow. `send.rs:228` uses `input_sat.checked_sub(payment_sat + fee_with_change)`, but the inner `payment_sat + fee_with_change` itself overflows before `checked_sub` sees it. A single payment of `u64::MAX` (which passes the `>= DUST` check at `send.rs:165-169`, since there is no `MAX_MONEY` bound anywhere) causes `payment_sat + needed_fee` to wrap to roughly `needed_fee - 1`, so the `NotEnoughFunds` guard is bypassed. The resulting `SignableTransaction` carries a `TxOut` worth `u64::MAX` satoshis, gets FROST-signed by `TransactionSignMachine::sign` (`send.rs:355-398`), and is consensus-invalid — it can never confirm.

### Impact Explanation
The threshold multisig produces valid BIP-340 signatures for a transaction that was supposed to be rejected by the balance check — i.e., an integer overflow defeats a security-relevant validation on attacker-controlled values, directly paralleling the CVE class. The scheduler will treat the plan as in-flight (payments consumed/queued at the wrapped change amount), yet the transaction can never be accepted by the Bitcoin network, permanently stalling the plan and its queued payments. Repeated crafted instructions pin the payment queue in a state where `prepare_send` keeps producing unbroadcastable signatures. This is Medium: no key recovery or spendable-fund theft, but a persistent liveness/funds-freeze violation reachable by an unprivileged depositor crafting an `InInstruction` amount.

### Likelihood Explanation
Reachability: `processor/src/networks/bitcoin.rs:433-452` feeds `payment.balance.amount.0` — a `u64` originating from scale-decoded on-chain instructions — directly into `BSignableTransaction::new`. No bound against Bitcoin's `MAX_MONEY` (21M BTC) exists in `send.rs`, and `payment.balance.amount.0` is not clamped upstream in the shown paths. The overflow requires only a payment amount within `needed_fee` of `u64::MAX`. Caveat: overflow wraps only in release builds (`-C overflow-checks=off`); debug builds panic, which is a DoS-only outcome. The wrap path is the production configuration.

### Recommendation
Use checked/saturating arithmetic throughout `SignableTransaction::new`: compute `payment_sat` with `checked_add`/`try_fold`, validate each payment `<= MAX_MONEY` (or `<= input_sat`), and use `payment_sat.checked_add(needed_fee)` / `checked_add(fee_with_change)` returning `NotEnoughFunds` on overflow. Also make `fee()` (`send.rs:138-141`) use `checked_sub` for defense in depth.

### Proof of Concept
```rust
// networks/bitcoin: with a scanner-received input of e.g. 1_000_000 sats
let payment = (p2tr_script_buf(key).unwrap(), u64::MAX); // passes >= DUST
let tx = SignableTransaction::new(vec![output], &[payment], Some(change), None, FEE);
// payment_sat + needed_fee wraps to needed_fee - 1
// -> input_sat < wrapped_sum is false -> check bypassed
// -> checked_sub sees wrapped (payment_sat + fee_with_change) -> small Some(value)
// -> returns Ok(SignableTransaction) with a u64::MAX TxOut; sign() produces an
//    invalid transaction that can never confirm, stalling the plan
```

Uncertain: whether upstream scheduler code already bounds `payment.balance.amount.0` to `<= total inputs` before `prepare_send` — the snippets show `prepare_send` assumes `signable_transaction` will do the rejecting (`processor/src/networks/mod.rs:442-455`), consistent with the overflow being reachable.