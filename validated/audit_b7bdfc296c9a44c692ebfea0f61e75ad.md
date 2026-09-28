### Title
Attacker-deposited sub-spend-cost dust outputs are treated as spendable inputs, burning vault funds to fees — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_transaction` registers *every* on-chain output paying to a registered Serai address as a `ReceivedOutput`, with no minimum-value check. `SignableTransaction::new` then sums these outputs as inputs and pays `fee_per_vbyte * vbytes` per input. Any unprivileged party can send Bitcoin transactions creating outputs whose value is below the cost to spend them (e.g., 546 sat outputs costing ~57 vbytes each at any positive fee rate). This is the direct analog of the reported bug: an attacker seeds the system state such that a subsequent, victim-executed operation consumes economically worthless residue while paying the full cost of processing it — the missing `minAmountTokens` check maps to the missing minimum received-output value.

### Finding Description
- `Scanner::scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-214` pushes a `ReceivedOutput` for any `tx.output` whose `script_pubkey` matches a registered script, checking only the script — never `output.value`.
- `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:150-256` accepts `Vec<ReceivedOutput>` as inputs, computes `input_sat` as the plain sum of `input.output.value` (line 175), and charges each input ~57 vbytes of weight against `fee_per_vbyte`. There is no filter dropping inputs whose `value` is less than `fee_per_vbyte * 57` (their marginal spend cost).
- Consequently each attacker-created dust input contributes less than it costs: the difference is silently consumed from the other inputs' value as fee. If such inputs push `input_sat - payment_sat` below the required fee, `new` either burns the remainder as excess fee (sub-`DUST` change at lines 223–235 is dropped into `needed_fee`) or fails with `NotEnoughFunds`, stalling the spend — mirroring the original report's "1 wei left, gas to finalize exceeds value" outcome.

### Impact Explanation
An attacker who learns a registered deposit address (all deposit addresses are necessarily public) can flood it with minimal-value outputs. Each accepted input is a guaranteed net loss to the Serai vault equal to `fee_cost(input) − value`, paid to miners, and can wedge transactions into `NotEnoughFunds`/`NoOutputs` states that still consumed scheduling and signing effort. This is a griefing/fee-drain vector reachable entirely with public Bitcoin transactions.

### Likelihood Explanation
Low-to-moderate frequency: it requires the attacker to spend real dust outputs (bounded cost ~dust value per output), and the profit is only denial-of-service/fee burn rather than theft. However it is fully permissionless and repeatable at will, and on Bitcoin dust outputs are cheap to create in bulk (a single transaction can produce dozens of outputs to the same script).

### Recommendation
Enforce a minimum received value at scan/acceptance time, analogous to the recommended `minAmountTokens`:
- In `Scanner::scan_transaction` (or when constructing inputs for `SignableTransaction::new`), drop outputs with `value < fee_per_vbyte * marginal_input_vbytes` — i.e., any output that costs more to spend than it contributes.
- Alternatively/additionally, in `SignableTransaction::new`, prune inputs where `input.value() < fee_per_vbyte * 57` before computing `input_sat`, so dust never degrades the spendable balance.

### Proof of Concept
```rust
// networks/bitcoin context
let mut scanner = Scanner::new(group_key).unwrap();
// attacker sends a tx with an output worth 546 sat to the vault's P2TR script
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs.len(), 1);          // accepted unconditionally
assert_eq!(outputs[0].value(), 546);   // below spend cost at any fee rate > ~9.5 sat/vB

// When building a spend, the dust input is summed into input_sat
// while adding ~57 vbytes of fee. At fee_per_vbyte = 10:
// marginal fee = 570 sat > 546 sat contributed -> net -24 sat per input,
// silently burned as excess fee (sub-DUST change is dropped, send.rs:223-235).
```

Note: the `processor` crate documents `const DUST = 10_000` with the intent that received outputs exceed spend cost (`processor/src/networks/mod.rs:295-301`), but that filter lives outside the in-scope `networks/bitcoin` crate — within the in-scope wallet code, no such minimum is enforced on scanned outputs, so the guard depends entirely on upstream callers remembering to apply it.