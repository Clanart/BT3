### Title
Missing upper-bound validation on payment amounts allows `u64` wraparound bypass of the `NotEnoughFunds` check — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` validates only that each payment amount is ≥ `DUST` (546 sat) and that `sum(inputs) >= sum(payments) + needed_fee`. Both the payment sum (`payment_sat`) and the solvency check are performed with plain wrapping `u64` arithmetic. An unprivileged party who can cause payments to be scheduled (e.g., withdrawal instructions feeding the multisig scheduler) can supply amounts near `u64::MAX` that wrap `payment_sat` to a small value, defeating the `NotEnoughFunds` check and producing a `SignableTransaction` whose outputs vastly exceed its inputs — which the FROST multisig then signs.

### Finding Description
In `SignableTransaction::new`:

- The only per-payment check is a lower bound: `if *amount < DUST { Err(DustPayment) }` (line 165-169). There is no upper bound — values like `u64::MAX` are accepted even though they cannot exist on Bitcoin (max supply ~21e6·1e8 sat).
- `payment_sat` is computed with `payments.iter().map(|payment| payment.1).sum::<u64>()` (line 187), which silently wraps on overflow in release builds.
- The solvency guard `if input_sat < (payment_sat + needed_fee)` (line 215) then compares `input_sat` against the wrapped sum, so `NotEnoughFunds` is never raised.
- The resulting `Transaction` (line 245-256) carries `TxOut`s whose summed value exceeds `MAX_MONEY` and exceeds the inputs, and it flows into `multisig()` → `TransactionSignMachine::sign`, which produces valid BIP-340 FROST signatures over `taproot_key_spend_signature_hash` for this consensus-invalid transaction (lines 383-390).
- Additionally, `fee()` subtracts output sum from input sum with plain `-` (line 139-140); with outputs > inputs this underflows — a panic in debug builds and a garbage ~`u64::MAX` "fee" in release builds — corrupting any fee accounting downstream (the processor uses `tx.fee()` / `needed_fee` when amortizing costs and when reconciling forwarded outputs, e.g. `instruction.balance.amount.0 -= tx.0.fee()`).

The same wrapping issue applies to `input_sat` (line 175) and to `fee_per_vbyte * vbytes` / `payment_sat + needed_fee` (lines 206, 215), which are also unchecked multiplications/additions.

### Impact Explanation
The threshold multisig signs a transaction it would never intend to sign: one claiming outputs larger than its inputs. Although such a transaction is consensus-invalid and cannot be mined, the signing pipeline treats it as a normal plan: shares are produced and `complete()` returns the transaction as successful. Downstream accounting keys off `SignableTransaction` state — `fee()` underflows/panics (denial of service in the signing pipeline or wildly wrong fee values), branch outputs and forwarded-output instructions are recorded against a transaction that can never confirm, and payments are marked consumed while the funds were never moved. This is the Serai analog of the reported bug: a fee/amount relationship is computed and "proved" solvable without any check that the components are individually representable, letting wrapped arithmetic make an insolvent transaction appear solvent.

### Likelihood Explanation
Payment amounts originate from untrusted scheduling inputs (instructions/payments queued for the multisig) rather than anything cryptographically constrained. Any caller of `SignableTransaction::new` — a public API — can supply `payments` with values ≥ `u64::MAX - k` and trigger the wraparound deterministically. Exploitation requires only that two or more payments (or one payment plus `needed_fee`) sum past `u64::MAX`, with `payment_sat + needed_fee` wrapping below `input_sat`.

### Recommendation
Validate each `payment.1` against Bitcoin's `MAX_MONEY` (21e6 · 10^8 sat) and use checked arithmetic throughout `SignableTransaction::new`: `checked_add`/`checked_mul` for `payment_sat`, `payment_sat + needed_fee`, `input_sat`, and `fee_per_vbyte * vbytes`, returning a new `TransactionError::InvalidAmount` on overflow. Use `checked_sub` in `fee()`. This mirrors the report's remediation — a range/bound proof on `transfer_amount - fee` — by enforcing that every quantity and every intermediate sum stays within the representable domain before the transaction is deemed signable.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs — SignableTransaction::new
// One input of 1_000_000 sat, two payments of u64::MAX each.
let inputs = vec![received_output_with_value(1_000_000)]; // any ReceivedOutput
let payments = vec![
  (attacker_script_a(), u64::MAX),
  (attacker_script_b(), u64::MAX),
];

let stx = SignableTransaction::new(inputs, &payments, None, None, 1).unwrap();
// payment_sat wraps to u64::MAX - 1 (≈2^64 - 2)
// needed_fee = 1 * vbytes (small); check becomes
//   1_000_000 < (u64::MAX - 1) + small  -> wraps -> comparison bypassed
// stx is constructed with outputs totalling ~2^65 sat against 10^6 sat of inputs.

// FROST path then signs it:
let machine = stx.clone().multisig(&keys).unwrap();
let (sign_machine, preprocess) = machine.preprocess(&mut OsRng);
// ... shares exchanged, complete() returns a signed Transaction whose
// outputs exceed inputs and exceed MAX_MONEY.

// Fee accounting corrupts/panics:
let fee = stx.fee(); // sum(prevouts) - sum(outputs) underflows u64
```