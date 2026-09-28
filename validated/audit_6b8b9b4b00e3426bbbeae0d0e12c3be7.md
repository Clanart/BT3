### Title
`SignableTransaction::new` accepts an unbounded `fee_per_vbyte` with no maximum-fee bound, so leftover input value is burned as miner fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The referenced bug class is a swap executed with `amountOutMinimum = 0` and no `deadline`: a caller-supplied transaction is executed with no bound protecting the user from a disastrously bad output. The analog in Serai's in-scope code is the fee handling in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`: `fee_per_vbyte` is an externally supplied parameter that is multiplied by the transaction's virtual size and subtracted from the input total with only a *lower* bound (`TooLowFee`) and no *upper* bound. Whatever is left over after payments and this fee either goes to change (only if `>= DUST`) or is silently burned as additional fee. A malformed or attacker-influenced `fee_per_vbyte` therefore produces a signed transaction that gives nearly the entire input value to miners.

### Finding Description
`SignableTransaction::new` computes `needed_fee = fee_per_vbyte * vbytes` at line 206 and only enforces `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` at lines 211-213. There is no check that `needed_fee` is reasonable relative to the input value — no "fee cap" equivalent of `amountOutMinimum`/`amountInMax`, and no equivalent of a `deadline`/freshness check on the provided rate. When a change output exists, the leftover `input_sat - payment_sat - fee_with_change` is only returned to the wallet if it clears `DUST` at lines 228-234; otherwise it is added to the fee. When `change` is `None`, the doc comment at lines 145-147 states explicitly that "all leftover funds will become part of the paid fee", and `fee()` at lines 138-141 confirms the actual fee is `sum(inputs) - sum(outputs)`, unbounded by `needed_fee`.

Since the downstream `TransactionMachine::sign` signs `Prevouts::All` for the already-constructed transaction (lines 373-390), the threshold signature commits to this fee burn: every input's value is committed, and the difference is irrevocably paid to miners once broadcast.

### Impact Explanation
Loss of funds for the threshold wallet (and hence the users whose deposits back it). A `fee_per_vbyte` far above market rate — whether from a manipulated fee estimator, a crafted caller input, or a saturated `vbytes` multiplied by a modest rate — causes `input_sat - payment_sat - needed_fee` to be paid to miners instead of returned as change. Unlike the dust edge case, this is not bounded to < 546 sats: with `change: None`, or whenever the post-fee leftover falls below `DUST`, arbitrarily large sums are burned. The only thing standing between the wallet and total loss is `NotEnoughFunds` when the fee exceeds inputs entirely — a fee equal to `input_sat - payment_sat` passes all checks.

### Likelihood Explanation
`fee_per_vbyte`, `payments`, and `change` are all caller-supplied arguments to `SignableTransaction::new`; none are clamped against any sanity bound (e.g., a multiple of `DEFAULT_MIN_RELAY_TX_FEE`, a maximum sat/vbyte, or a percentage of input value). Any component or caller that constructs transactions from partially untrusted inputs — a fee oracle, an RPC-provided estimate, or an instruction-driven builder — can feed an inflated rate or omit `change`, and the resulting transaction will be FROST-signed verbatim because `sign` rejects only a non-empty `msg` and signs whatever `self.tx.tx` contains. This mirrors the original finding: the protective bound parameter exists in shape (`fee_per_vbyte` exists) but is used with no bound on the harm it can do, exactly as `amountOutMinimum: 0` provided no protection.

### Recommendation
- Reject `fee_per_vbyte` above a sane maximum (e.g., a configurable cap or a multiple of `DEFAULT_MIN_RELAY_TX_FEE`) with a `TooHighFee` error.
- Cap the *effective* fee: error if `fee()` (actual `inputs - outputs`) exceeds `needed_fee` by more than the dust-threshold remainder, instead of silently donating leftovers.
- Require `change` for transactions where the expected leftover exceeds `DUST`, or expose the effective fee to signers for confirmation before `multisig()`/`sign()` proceeds.

### Proof of Concept
```rust
// Networks/bitcoin wallet context; inputs are ReceivedOutput values controlled
// by the wallet, payments/change/fee_per_vbyte supplied by the caller.
let inputs = vec![received_output_with_value(1_000_000)];   // 0.01 BTC
let payments = &[(p2tr_script_buf(dest), 100_000)];         // small payment

// Attacker-influenced (or buggy) fee rate: 10000 sat/vbyte on a ~150 vB tx
// -> needed_fee ~= 1_500_000 > input_sat -> NotEnoughFunds; so use 5900 sat/vB:
let stx = SignableTransaction::new(
  inputs,
  payments,
  None,              // no change -> ALL leftover becomes fee
  None,
  5_900,             // passes the TooLowFee check; no upper bound exists
).unwrap();

// needed_fee ~ 885_000; actual fee = inputs - outputs = 1_000_000 - 100_000
//                                        = 900_000 sats  (90% of the input!)
assert_eq!(stx.fee(), 900_000);
// The threshold signature (TransactionMachine::sign, Prevouts::All) commits to
// this burn; there is no parameter analogous to amountOutMinimum to stop it.
```

The same path with `change: Some(addr)` burns `input_sat - payment_sat - fee_with_change` whenever that leftover is `< DUST`, and still leaves `fee_per_vbyte` itself uncapped — the missing bound, not the dust edge, is the root cause.