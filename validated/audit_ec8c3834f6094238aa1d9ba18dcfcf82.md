### Title
Dust/uneconomical outputs are credited by `Scanner::scan_transaction` and spent by `SignableTransaction::new` with no minimum-input check, letting an external sender burn multisig funds on fees - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report describes a ratio truncating to zero so a borrower receives funds while posting effectively nothing (a value-below-threshold / missing `> 0` guard bug class). The analog in `bitcoin-serai` is the receive/spend path: `Scanner::scan_transaction` credits *any* output paying to a registered script regardless of value, and `SignableTransaction::new` enforces a dust minimum only on *payments*, never on *inputs*. An unprivileged third party can send the multisig outputs whose value is positive but below the cost of spending them; they are reported as received balance and later consumed as inputs, where each input adds ~57 vbytes of fee while contributing less than that in value — a net drain on the wallet's funds.

### Finding Description
`Scanner::scan_transaction` pushes a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script, with no lower bound on `output.value`:

- `networks/bitcoin/src/wallet/mod.rs:199-214` — `scan_transaction` clones the `TxOut` unconditionally once the script matches.
- `networks/bitcoin/src/wallet/send.rs:32` — `DUST = 546` is defined but only applied to payments: `send.rs:165-169` rejects `payment.1 < DUST`, while inputs are summed blindly at `send.rs:175` (`input_sat = inputs.iter().map(...).sum()`).
- `send.rs:204-213` — `needed_fee = fee_per_vbyte * vbytes` where `vbytes` grows ~57 vbytes per input (per the weight model in `calculate_weight_vbytes`, `send.rs:62-127`). An input of e.g. 546–999 sats costs more to spend than it contributes at any fee rate ≥ ~10 sat/vb; a mined non-standard output can even be 1 sat.

The thresholds are asymmetric for the same reason `collateralFor` was in the report: the code treats "nonzero / above dust" as "worth something," when the economically meaningful check is `value > fee_to_spend`. Nothing in the crate filters or rejects such inputs — `multisig()` (`send.rs:273-285`) only validates the offset/script binding, not the value.

### Impact Explanation
- Funds are reported as received (`ReceivedOutput::value() > 0`) that are not economically spendable — spending them strictly decreases wallet value, analogous to collateral that exists on paper but is zero in practice.
- An attacker can repeatedly dust the threshold wallet's address(es). Each forced-spend or aggregation transaction that includes these inputs burns `57 * fee_per_vbyte - value` satoshis of the group's funds per input, per input, attacker-chosen cost (a few hundred sats) for a few-hundred-sat loss — repeatable indefinitely until mitigations are added downstream. The downstream scheduler's `DUST = 10_000` (processor) acknowledges exactly this risk class, but the `bitcoin-serai` crate itself provides no guard and its `Scanner`/`SignableTransaction` API is what external callers rely on.

### Likelihood Explanation
Any party who learns a wallet address/registered script (public on-chain after the first receive) can craft the dust outputs at trivial cost. Inclusion is not optional at this layer: `scan_transaction`/`scan_block` return all matching outputs, and `SignableTransaction::new` consumes whatever `ReceivedOutput`s it is given. The only prerequisite is a fee rate high enough that `57 * fee_per_vbyte > output_value`, which holds for all sub-1000-sat outputs at ≥10 sat/vb and for all sub-5700-sat outputs at 100 sat/vb.

### Recommendation
- Track a minimum economical input value in `bitcoin-serai` (e.g. `MIN_INPUT_VALUE` derived from the ~57-vbyte spend cost at a conservative fee rate, mirroring the processor's 10 000-sat `DUST` rationale) and refuse to return matching outputs below it from `Scanner::scan_transaction`, or
- Add a per-input check in `SignableTransaction::new` (`input.output.value.to_sat() >= MIN_INPUT_VALUE`) returning an error, so dust inputs can never be silently signed for regardless of the caller.

### Proof of Concept
```rust
// Attacker learns the wallet script from a prior deposit, then sends N outputs
// of 546 sats each (passes relay dust rules) to it.

let mut scanner = Scanner::new(group_key).unwrap();
let received = scanner.scan_transaction(&attacker_tx);
// received.len() == N, each ReceivedOutput::value() == 546 -- reported as funds

// Later, the wallet builds a spend including these inputs:
let stx = SignableTransaction::new(
    received,               // N inputs worth 546 sats each
    &[(pay_script, 10_000)],
    Some(change_script),
    None,
    100,                    // 100 sat/vb fee rate
).unwrap();

// Each input contributes 546 sats but adds ~57 vb => ~5700 sats of fee.
// Net effect per input: -5154 sats drained from the multisig's change/fee.
// inputs' total value is positive yet the wallet is strictly worse off
// than if it had never received them -- identical in spirit to
// collateralFor == 0 while loan.amount > 0.
```