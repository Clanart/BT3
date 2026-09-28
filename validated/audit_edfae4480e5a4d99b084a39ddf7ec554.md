### Title
Refunds are sent to the spender of `tx.input[0]`, not to the actual depositor — ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
The Cooler bug class — funds delivered to a fixed, wrong party (contract owner) instead of the party who initiated the action (loan requester) — maps onto bitcoin-serai's refund-origin attribution. When a Bitcoin deposit carrying an `InInstruction` cannot be executed, Serai refunds the depositor. The refund destination (`presumed_origin`) is derived from whoever spent `tx.input[0]`, which is not necessarily the depositor. Any transaction paying a Serai multisig address in which `input[0]` is controlled by a third party causes the refund to be issued to that third party.

### Finding Description
In `get_outputs`, every scanned output in a transaction is assigned `presumed_origin` from the output spent by `tx.input[0]`: the code unconditionally reads `let input = &tx.input[0]`, fetches its previous output, and uses `Address::new(spent_output.script_pubkey)` as the origin for *all* outputs in the transaction. The inline comment even acknowledges this heuristic is approximate ("This may identify the P2WSH output embedding the InInstruction as the origin... TODO"). [1](#0-0) 

This `presumed_origin` is what drives refunds: scanned deposits that cannot be processed are turned into `PlanFromScanning::Refund(output, refund_to)` plans, which are signed and paid out to `refund_to`. [2](#0-1) 

Nothing binds `refund_to` to the party that actually funded the deposit output or authored the `InInstruction`. In any transaction with multiple inputs — a PayJoin/coinjoin-style transaction, an exchange batch spend, or simply a wallet whose `input[0]` ordering differs from the depositor's intended refund address — input 0 may be an output controlled by (or formerly controlled by) someone other than the depositor. An unprivileged attacker can also deliberately arrange this: they craft or co-author a transaction that creates a Serai-bound output with malformed/unexecutable `InInstruction` data while ensuring their own output occupies `input[0]`, so the protocol refunds the deposit value to them.

### Impact Explanation
Funds that should be returned to the depositor are paid to the spender of `input[0]` instead — a direct "funds sent to the wrong person" loss identical in shape to the Cooler finding. The depositor's collateral (the Bitcoin output) is consumed by the multisig, while the refund is routed to an unrelated or attacker-controlled address, and there is no recovery path once the refund plan is signed and broadcast.

### Likelihood Explanation
The trigger requires a deposit transaction where `input[0]` is not the depositor's address, plus a failed/unexecutable instruction so the refund path is taken. Collaborative transactions (PayJoin, coinjoin, batch spends) routinely mix inputs from multiple parties, and input ordering is arbitrary consensus-wise, so this is reachable with purely public transaction construction. An attacker can also proactively construct such a transaction to capture refunds.

### Recommendation
Attribute refunds per-input or per-depositor rather than globally to `input[0]`: for example, require the `InInstruction` to explicitly carry the refund address (and treat missing/invalid data as a refund to a provably depositor-controlled input), or associate each scanned output with the inputs that funded it by proportional contribution, or at minimum document and enforce that only `input[0]`'s owner can initiate refundable deposits. The `presumed_origin` heuristic should not be used as an authoritative payment destination.

### Proof of Concept
1. Victim V (or attacker A in a collaborative flow) constructs a Bitcoin transaction with at least two inputs: `input[0]` spends an output whose `script_pubkey` resolves to A's address; another input is V's. One output pays `p2tr_script_buf(multisig_key)` — the `External` scanner script — and the transaction embeds malformed or unexecutable Serai `InInstruction` data (or is otherwise routed to the refund path).
2. `Bitcoin::get_outputs` scans the block, matches the multisig `script_pubkey` in `Scanner::scan_transaction`, marks the output `OutputType::External`, attaches the data, and sets `presumed_origin = Address::new(input[0].previous_output.script_pubkey)` = A's address.
3. `extract_serai_data` fails to produce an executable instruction; the output becomes `PlanFromScanning::Refund(output, refund_to = A)` in `processor/src/multisigs/mod.rs`.
4. The scheduler builds a plan paying A; validators threshold-sign it; A receives the refunded BTC. V's deposit is spent by the multisig and V receives nothing — the same outcome as Person B in the Cooler report (collateral deposited, proceeds sent to the wrong party).

### Citations

**File:** processor/src/networks/bitcoin.rs (L707-736)
```rust
      let presumed_origin = {
        // This may identify the P2WSH output *embedding the InInstruction* as the origin, which
        // would be a bit trickier to spend that a traditional output...
        // There's no risk of the InInstruction going missing as it'd already be on-chain though
        // We *could* parse out the script *without the InInstruction prefix* and declare that the
        // origin
        // TODO
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
      }
```

**File:** processor/src/multisigs/mod.rs (L584-596)
```rust
          .map(|plan| match plan {
            PlanFromScanning::Refund(output, refund_to) => {
              let existing = self.existing.as_mut().unwrap();
              if output.key() == existing.key {
                Self::refund_plan(&mut existing.scheduler, txn, output, refund_to)
              } else {
                let new = self
                  .new
                  .as_mut()
                  .expect("new multisig didn't expect yet output wasn't for existing multisig");
                assert_eq!(output.key(), new.key, "output wasn't for existing nor new multisig");
                Self::refund_plan(&mut new.scheduler, txn, output, refund_to)
              }
```
