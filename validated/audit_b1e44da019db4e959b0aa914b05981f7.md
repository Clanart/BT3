### Title
Scanner reports dust-valued outputs as spendable funds — deposits credited that can never be spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` (and therefore `Scanner::scan_block`) reports any output whose `script_pubkey` matches a registered script as a spendable `ReceivedOutput`, with no check on the output's value. An unprivileged sender can create a transaction paying a dust-valued amount (0–545 sats, below the `DUST` constant of 546 that the same crate enforces on outgoing payments) to a Serai multisig/forwarding address. The scanner reports it as received funds, yet `SignableTransaction` treats values below `DUST` as non-viable and the cost to spend such an input at any reasonable fee rate exceeds its value. This is the Serai analog of the reported bug class: an advertised/credited balance that is not actually backed by spendable funds — the claimant (here, the depositor/protocol crediting the deposit) is promised value that cannot be delivered.

### Finding Description
`scan_transaction` matches outputs purely on `script_pubkey` membership in `self.scripts` and pushes a `ReceivedOutput` unconditionally:

- `networks/bitcoin/src/wallet/mod.rs:199-214` — no `output.value` check before constructing `ReceivedOutput`.
- `networks/bitcoin/src/wallet/mod.rs:88-97` — `ReceivedOutput` is documented as "A spendable output" and exposes `value()`; nothing downstream in this crate records that the value may be uneconomical.
- `networks/bitcoin/src/wallet/send.rs:32` — `pub const DUST: u64 = 546`, and `SignableTransaction::new` rejects *payments* below `DUST` (`send.rs:165-169`) and only emits *change* if `value >= DUST` (`send.rs:228-233`). The crate therefore knows sub-546-sat outputs are not viable, yet accepts them on the receive path.
- Contrast with `scan_block`'s coinbase handling (`mod.rs:216-220`): the immature-output hazard is documented and callers are told to filter; the dust hazard is neither filtered nor documented.

An attacker crafteds a transaction to the external/branch/change/forwarded P2TR script with `value < 546` (e.g., 330 sats, the actual Taproot relay dust limit, so the transaction relays and confirms). `scan_transaction` returns it as a normal received output indistinguishable from a legitimate deposit.

### Impact Explanation
Funds are reported received that are not spendable:

1. Any consumer crediting deposits based on `Scanner` output credits the depositor for value the multisig cannot realize — spending the input costs more in fees than its value, and batching it into a `SignableTransaction` consumes weight (fee) while contributing ~nothing.
2. If the dust output is included among inputs to satisfy a larger payment, `input_sat` includes dust (`send.rs:175`), so `NotEnoughFunds` checks can pass while the *effective* spendable balance is lower — the same "promised payout exceeds available balance" shape as the original report: a claim/withdrawal that reverts or underpays because the reported balance was never real.
3. Repeated dust deposits (cheap for the attacker at ~330 sats each) inflate the tracked balance and force ever-larger transactions if consumed, converting a small attacker cost into persistent protocol loss or stuck funds.

### Likelihood Explanation
Fully reachable by an unprivileged party: sending a Bitcoin transaction with a dust-valued output to a known Serai address requires no privileges, no collusion, and no malformed encoding — just consensus-valid transaction data, which the threat model explicitly includes. On regtest/mainnet a ~330-sat P2TR output is relayed and mined. The only mitigation observed is the processor's `>= N::DUST` filter in `processor/src/multisigs/scanner.rs:564`, which is out of scope and not part of the `bitcoin-serai` API contract; the library itself offers no documented warning or filtering, so any integrator using `scan_transaction`/`scan_block` directly is exposed.

### Recommendation
Filter outputs in `Scanner::scan_transaction` by a minimum value (e.g., `output.value.to_sat() >= DUST`), or at minimum document on `Scanner`/`ReceivedOutput` that callers must discard outputs whose value is below the spendable threshold. Consistent with how the coinbase-maturity hazard is documented for `scan_block`, the dust hazard should be enforced or explicitly documented for the receive path.

### Proof of Concept
```rust
// Attacker sends a consensus-valid tx with a 330-sat output to the
// Serai multisig's external P2TR script (330 >= Taproot relay dust, < 546).
let dust_tx = Transaction {
  // ... input spending attacker's coin ...
  output: vec![TxOut {
    value: Amount::from_sat(330),
    script_pubkey: p2tr_script_buf(group_key).unwrap(),
  }],
  ..
};

// scan_transaction reports it as a spendable ReceivedOutput:
let received = scanner.scan_transaction(&dust_tx);
assert_eq!(received.len(), 1);          // credited as received
assert_eq!(received[0].value(), 330);   // below DUST (546)

// SignableTransaction can never economically spend it:
// - as a payment target it would be rejected (DustPayment)
// - as an input, fee(1 input) > 330 sats at any fee rate >= ~4 sat/vB,
//   yet its 330 sats still count toward input_sat, inflating the
//   "available balance" used by the NotEnoughFunds check.
```

The deposit is scanned and reportable while the value it represents is unrecoverable — the direct analog of a bounty advertising a payout the contract balance cannot cover.