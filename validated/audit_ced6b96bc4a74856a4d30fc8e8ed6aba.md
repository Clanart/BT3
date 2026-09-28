### Title
Unprivileged senders can register zero-value/sub-dust outputs that Serai treats as spendable, forcing fee-burning spends at no cost - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external finding's bug class is: *an operation that should carry a mandatory economic cost becomes permissionless and free when the guarding parameter is zero, letting an unprivileged party drain protocol value.* The Serai analog lives in the Bitcoin wallet/scanner code: `Scanner::scan_transaction` registers **any** output paying to a registered script as a `ReceivedOutput` with no minimum-value check, and `SignableTransaction::new` enforces the `DUST` floor only on *payments*, never on *inputs*. An attacker who sends a zero-value or sub-dust output to a Serai multisig script creates a "received output" that is economically unspendable — the marginal fee to spend it exceeds its value — yet Serai will report it, track it, and (once scheduled) sign transactions that spend it at a net loss.

### Finding Description
`Scanner::scan_transaction` iterates every `TxOut` of every transaction and pushes a `ReceivedOutput` for any output whose `script_pubkey` matches a registered script, unconditionally (`networks/bitcoin/src/wallet/mod.rs:199-214`). There is no `output.value` check. A `ReceivedOutput` with `value == 0`, or any value below the `DUST = 546` constant (`networks/bitcoin/src/wallet/send.rs:32`), is indistinguishable from a genuine deposit.

When such outputs are later consumed, `SignableTransaction::new`:

- rejects dust only in the `payments` list (`send.rs:165-169`);
- accepts `inputs` of any value, summing them into `input_sat` (`send.rs:175`);
- requires only that `input_sat >= payment_sat + needed_fee` in aggregate (`send.rs:215-221`).

Each P2TR key-spend input adds ~58 vbytes of weight. A 0-sat (or 1-sat) input therefore contributes ~0 sats while costing ~58+ sats of fee at minimum relay rate. `fee()` then reports the burned difference as the transaction's fee (`send.rs:138-141`), and the multisig signs the spend via `TransactionMachine`/`taproot_key_spend_signature_hash` (`send.rs:373-397`).

This is the direct analog: just as `forceDeallocatePenalty == 0` removes the cost barrier that implicitly authorizes `forceDeallocate`, the absent minimum-value floor removes the cost barrier on "create a Serai-tracked UTXO". The attacker pays nothing (a zero-value output is consensus-valid; sub-dust outputs are consensus-valid and merely nonstandard to relay), while every aggregation/spend Serai signs containing that input destroys protocol funds in fees — the same shape as adapters being deallocated at no cost to the caller while vault suppliers absorb the loss.

The processor-layer "flat fee per input" described in `spec/processor/UTXO Management.md` cannot compensate here: a flat fee is recovered from the output's own credited value, and a zero-value output has no value to deduct from, so the cost imposition is free — precisely the `penalty = 0` condition.

### Impact Explanation
An unprivileged external party (anyone who can get a transaction mined on Bitcoin) can:

1. Cause Serai to report "funds received" that are not spendable — a zero-value output is recorded by `scan_transaction`/`scan_block` as a `ReceivedOutput` and flows downstream as an `External` output, despite carrying no recoverable value.
2. Force Serai multisigs to sign transactions whose fee exceeds the contributed input value, burning the multisig's balance. Repeated dust/zero outputs compounding into `MAX_INPUTS`-bounded aggregation transactions amplify the loss multiplicatively.
3. Permanently poison the UTXO set: because `scan_block`/`scan_transaction` cannot distinguish the poisoned output, it remains a candidate input for every future plan until spent at a loss.

This matches the report's medium-severity profile: no key material is compromised, but the protocol's funds are deallocated through an operation the attacker performs for free.

### Likelihood Explanation
Reachability is high: the only requirement is a Bitcoin transaction creating a zero-value or sub-546-sat output to a known Serai P2TR script (the multisig script is derivable from the public group key; `p2tr_script_buf` at `mod.rs:80-86`). Zero-value outputs are consensus-valid and are routinely minable (e.g., via direct miner submission or any transaction also paying a fee elsewhere). No position in Serai, no allowance, and no threshold cooperation is needed — identical to the finding's "Carl has never deposited" precondition once the penalty (here, the missing value floor) is zero. Exploitation depends on the downstream scheduler including the output in a spend, which the code does nothing to prevent — `SignableTransaction::new` never rejects an unprofitable input.

### Recommendation
- In `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs`), drop outputs whose `output.value` is below `DUST` (or a configurable minimum representing the marginal spend cost), so uneconomical outputs are never reported as received funds.
- Alternatively/additionally, in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), reject or filter any `ReceivedOutput` whose `value` is less than the marginal fee its input adds (`fee_per_vbyte * per-input vbytes`), rather than only checking `input_sat >= payment_sat + needed_fee` in aggregate.
- If zero-value outputs must still be scannable for accounting, classify them distinctly (e.g., non-spendable) so they are never selected as transaction inputs — mirroring the report's recommendation of a separate "ping" function rather than a zero-cost path through the privileged operation.

### Proof of Concept
```rust
// Attacker crafts a Bitcoin tx paying 0 sats (or 1 sat) to Serai's P2TR script.
// The script is publicly derivable from the multisig group key.
let serai_script = p2tr_script_buf(group_key).unwrap();
let attacker_tx = Transaction {
    // ... any funding input the attacker controls / arranges to be mined ...
    output: vec![TxOut {
        value: Amount::ZERO,                 // "penalty = 0": costs the attacker nothing
        script_pubkey: serai_script.clone(),
    }],
    ..
};

// Inside every Serai node, the scanner registers it unconditionally.
let mut scanner = Scanner::new(group_key).unwrap();
let received = scanner.scan_transaction(&attacker_tx);
assert_eq!(received.len(), 1);                       // reported as a real received output
assert_eq!(received[0].value(), 0);                  // networks/bitcoin/src/wallet/mod.rs:205-211

// Later, when the processor schedules a spend including this input,
// SignableTransaction::new accepts it — DUST is checked only on payments.
let signable = SignableTransaction::new(
    vec![received[0].clone(), legitimate_input],     // dust input included
    &payments,
    change,
    None,
    fee_per_vbyte,
).unwrap();

// The zero-value input adds ~58 vbytes of weight and contributes 0 sats.
// Serai's multisig signs the TX; fee() = sum(inputs) - sum(outputs) includes
// the full marginal cost of the attacker's free input, burning Serai funds.
let machine = signable.multisig(&keys).unwrap();
// ... FROST signing proceeds normally; the TX is valid and relayable ...
```

The absence of any `value` check in `scan_transaction` (`mod.rs:199-214`) and the payments-only `DUST` check plus aggregate-only solvency check in `SignableTransaction::new` (`send.rs:165-221`) are the root cause: the implicit economic barrier on creating spendable-looking UTXOs is set to zero, and any unprivileged sender can exploit it.

Note: I verified this within the in-scope Bitcoin wallet/scanner code. The processor-layer flat-fee mitigation in `spec/processor/UTXO Management.md` is out of scope and, per its own description, is recovered from the output's value — which is zero here, so it cannot neutralize the attack — but the scheduler's exact input-selection logic was not fully reviewed within the in-scope crates.