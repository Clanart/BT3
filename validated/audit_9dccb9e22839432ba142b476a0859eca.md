### Title
Refund origin attributed to the transaction's first input rather than the output's actual payer - (File: `processor/src/networks/bitcoin.rs`)

### Summary
Serai's Bitcoin `get_outputs` assigns every Serai-bound output in a transaction a `presumed_origin` taken solely from `tx.input[0]`'s spent output `script_pubkey`. When a depositor's `RefundableInInstruction` does not explicitly set `origin`, this transaction-global value — not the actual source of the funds backing that output — is used as the refund destination. Like the Unlock bug (refund computed from the *current* price rather than the price actually paid), Serai computes the refund's payer identity from a mutable transaction-level field rather than a per-output recorded payer.

### Finding Description
In `get_outputs`, after scanning a transaction's outputs, the code fetches `tx.input[0].previous_output`, retrieves the spent transaction, and sets `presumed_origin` to `spent_output.script_pubkey` for *all* outputs in the transaction (`processor/src/networks/bitcoin.rs:714-735`): [1](#0-0) 

The code itself acknowledges this is a guess: it notes the input may identify a P2WSH output embedding the `InInstruction` rather than a true origin, and marks the resolution as `TODO`. Downstream, `instruction_from_output` (called in `processor/src/multisigs/mod.rs:859` and `:903`) derives `refund_to` from the output's instruction data, and `presumed_origin` exists precisely to supply the refund address when `RefundableInInstruction.origin` is `None` (`substrate/in-instructions/primitives/src/shorthand.rs:41-43`, where `origin` is optional). A `Refund` plan then pays the full `output.balance()` to that address (`processor/src/multisigs/scheduler/utxo.rs:594-604`).

Bitcoin places no semantic constraint on input ordering. In collaboratively constructed transactions — PayJoin (BIP-78), CoinJoin, or any multi-party funding transaction — the counterparty (or coordinator) controls which input sits at index 0. A depositor who funds the Serai output from their own inputs but whose transaction is arranged so that `input[0]` belongs to the counterparty will have `presumed_origin` set to the counterparty's script. If the deposit later triggers a refund (e.g., the instruction is unexecutable), the refund is paid to the counterparty's address, not the depositor's.

### Impact Explanation
A depositor who relies on the implicit origin (`origin: None`, the common `Shorthand::transfer` path) can have the refund sent to an address controlled by a transaction collaborator rather than themselves, losing the entire refunded balance (`output.balance()`). Conversely, an attacker arranging the transaction can deliberately place their own input at index 0 to capture a refund of value they did not supply. This is direct value leakage to/from users — the same impact class as the Unlock finding (refund recipient/amount derived from a value other than what was actually paid in).

### Likelihood Explanation
Triggering requires the victim's deposit transaction to be collaboratively constructed and to lack an explicit `origin`, and the deposit must subsequently be refunded. PayJoin-style flows are a realistic pattern for privacy-conscious depositors, and input ordering is attacker-influenced there. The bug requires no validator misbehavior, no cryptography failure — only the misattribution of a public transaction field. Medium likelihood, consistent with the original report's downgraded medium severity.

### Recommendation
When `RefundableInInstruction.origin` is `None`, do not attribute refunds to `tx.input[0]`'s spent output. Options: require an explicit `origin` for refundability (refuse refunds when absent rather than guessing), or record per-depositor provenance at scan time and bind the refund to it. At minimum, document that `origin` must always be set by integrators, since the fallback is unreliable for any non-trivial transaction topology.

### Proof of Concept
1. Attacker and victim construct a collaborative Bitcoin transaction (e.g., a PayJoin where the victim contributes inputs to fund a Serai deposit output carrying a `RefundableInInstruction` with `origin: None`).
2. The attacker orders their own input as `tx.input[0]`.
3. `get_outputs` sets `presumed_origin` for the Serai output to the attacker's `script_pubkey` (`processor/src/networks/bitcoin.rs:714-729`).
4. The deposit is refunded (e.g., instruction not executable); `refund_plan` pays `output.balance()` to `refund_to` resolved from `presumed_origin` — the attacker's address (`processor/src/multisigs/scheduler/utxo.rs:594-602`, `processor/src/multisigs/mod.rs:903-907`).
5. The victim loses the refund; the attacker receives funds they never deposited.

**Uncertainty note:** I could not fully trace `instruction_from_output` (in `processor/src/networks/mod.rs`) to confirm whether `presumed_origin` is used directly as the refund destination or only to populate the emitted `InInstruction`'s origin field. If `presumed_origin` only feeds the substrate-side refund path rather than `refund_to`, the same misattribution applies one layer later, but the exact sink line is unverified.

### Citations

**File:** processor/src/networks/bitcoin.rs (L714-735)
```rust
        let spent_output = {
          let input = &tx.input[0];
          let mut spent_tx = input.previous_output.txid.as_raw_hash().to_byte_array();
          spent_tx.reverse();
          let mut tx;
          while {
            tx = self.rpc.get_transaction(&spent_tx).await;
            tx.is_err()
          } {
            log::error!("couldn't get transaction from bitcoin node: {tx:?}");
            sleep(Duration::from_secs(5)).await;
          }
          tx.unwrap().output.swap_remove(usize::try_from(input.previous_output.vout).unwrap())
        };
        Address::new(spent_output.script_pubkey)
      };
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
```
