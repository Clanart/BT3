### Title
Unfulfillable Plan is permanently committed as active, consuming its inputs and bricking the processor on reboot - ([File: processor/src/multisigs/mod.rs](processor/src/multisigs/mod.rs))

### Summary
Analogous to the Kleros `handleNotifiedRequest` issue (a one-way status transition to `AwaitingRuling` with no retry if the downstream step fails), Serai's `MultisigManager::plans_from_block` performs an irreversible state transition — `PlanDb::save_active_plan` persists the plan and the scheduler has already consumed its inputs — *before* knowing whether a `SignableTransaction` can actually be constructed. If `prepare_send` returns `tx: None` (fee exceeds input value, i.e. `TransactionError::NoOutputs | NotEnoughFunds`), the plan remains "active" forever with no retry path, its inputs are never restored (explicit TODO), its payments are dropped, and on the next reboot the processor panics in `MultisigManager::new` on `previously created transaction is no longer being created`.

### Finding Description
In `plans_from_block`, the plan is saved to the DB as active before the TX is known to be creatable: [1](#0-0) 

`prepare_send` returns `tx: None` when `needed_fee` returns `None`, which occurs whenever the transaction is unfulfillable at the current fee rate: [2](#0-1) 

For Bitcoin this happens on `TransactionError::NoOutputs | NotEnoughFunds`: [3](#0-2) 

When `tx` is `None`, nothing is pushed to `res`, no eventuality is registered, no signing protocol is started — yet the plan stays in `PlanDb` and the scheduler already ate the inputs. The code acknowledges the missing restoration but only "for efficiency's sake": [4](#0-3) 

The fatal part is on restart: `MultisigManager::new` replays all active plans and panics if a previously-None plan still produces `None` (the stored `block_number` makes the fee check deterministic, so it will): [5](#0-4) 

This mirrors the reported bug exactly: a state (`active plan` / `AwaitingRuling`) is entered that cannot be re-entered or exited when the actual send/acknowledgement never occurs. Just as `handleNotifiedRequest` cannot be called again because the status is no longer `Notified`, this plan cannot be re-planned because the inputs are gone and the plan is already "active" — and the reboot handler assumes active plans always had a creatable TX.

### Impact Explanation
- **Permanent loss of user funds**: the plan's payments (user Burns, or refunds/forwards) are silently dropped while the plan stays recorded as active; the burn is never executed and never retried.
- **Permanent processor liveness failure**: after any restart, the node panics loading the active plan, so the processor for that network can never boot again without manual DB surgery — exactly the "Kleros can never resolve the dispute" outcome.

### Likelihood Explanation
Reachable by an unprivileged party with public inputs:

1. Attacker sends an `External` output to the Serai multisig address with value `>= N::DUST` (so the scanner accepts it, `processor/src/multisigs/scanner.rs:564`) but small enough that spending/forwarding it is unfulfillable at the current median fee (`Bitcoin::median_fee`).
2. With a `refund_to` derivable instruction, a `PlanFromScanning::Refund` (or a `Forward` during rotation) is created and goes through `plans_from_block` → `PlanDb::save_active_plan` → `prepare_send` → `tx: None`.
3. Alternatively, during rotation, an attacker sends an External output to the *old* multisig, forcing a forwarding plan whose value is below the forwarding fee — same `None` path (see `ForwardedOutputDb` handling at `mod.rs:873-902`, which explicitly anticipates "this didn't create a TX").
4. Any ordinary user Burn whose amount is close to the fee rate when the fee spikes between scheduling and `prepare_send` hits the same path organically.

No collusion, no validator misbehavior required — just public transactions/burns plus a fee condition.

### Recommendation
- Do not call `PlanDb::save_active_plan` until `prepare_send` has returned `tx: Some(..)`; or record unfulfillable plans in a distinct "pending" state that is re-evaluated each block as fees change, rather than as `active`.
- On the `tx: None` path, restore the plan's inputs to the scheduler (implementing the TODO at `mod.rs:792`) and either re-queue the payments or emit an explicit failure, instead of leaving the plan active.
- Make `MultisigManager::new` tolerant of active plans that have no creatable TX (retry `prepare_send` at the current block instead of `panic!`), so a stuck plan cannot permanently brick the processor.

### Proof of Concept
1. At a block whose `median_fee` is `f`, the attacker sends an External output of value `v` to the multisig's `external_address` (or `forward_address` during `ForwardFromExisting`), where `v >= N::DUST` but `v < f * vbytes_for_single_input_spend`, embedding an `InInstruction` with a `refund_to` address.
2. Scanner accepts the output (`amount.0 >= N::DUST`, `scanner.rs:564`); `plans_from_scanning` emits `PlanFromScanning::Refund(output, refund_to)` (`mod.rs:906`).
3. `plans_from_block` calls `PlanDb::save_active_plan` (`mod.rs:726`), then `prepare_send` → `needed_fee` → `make_signable_transaction` → `BSignableTransaction::new` returns `NotEnoughFunds`/`NoOutputs` → `tx: None` (`bitcoin.rs:458`).
4. `res` gets nothing; no eventuality, no signing attempt, inputs consumed, `operating_costs` incremented by the would-be change. The refund is silently dropped.
5. Restart the processor: `MultisigManager::new` iterates `PlanDb::active_plans`, calls `prepare_send` at the same stored `block_number` (deterministic fee → still `None`), and hits `panic!("previously created transaction is no longer being created")` (`mod.rs:184-188`) — crash loop, signing for the network halts permanently.

### Citations

**File:** processor/src/multisigs/mod.rs (L176-193)
```rust
      for (block_number, plan, operating_costs) in PlanDb::active_plans::<N>(raw_db, key.as_ref()) {
        let block_number = block_number.try_into().unwrap();

        let id = plan.id();
        info!("reloading plan {}: {:?}", hex::encode(id), plan);

        let key_bytes = plan.key.to_bytes();

        let Some((tx, eventuality)) =
          prepare_send(network, block_number, plan.clone(), operating_costs).await.tx
        else {
          panic!("previously created transaction is no longer being created")
        };

        scanner
          .register_eventuality(key_bytes.as_ref(), block_number, id, eventuality.clone())
          .await;
        actively_signing.push((plan, tx, eventuality));
```

**File:** processor/src/multisigs/mod.rs (L726-745)
```rust
          PlanDb::save_active_plan::<N>(
            txn,
            key_bytes.as_ref(),
            block_number,
            &plan,
            running_operating_costs,
          );

          // If this Plan is from the scanner handler below, don't take the opportunity to amortze
          // operating costs
          // It operates with limited context, and on a different clock, making it nable to react
          // to operating costs
          // Despite this, in order to properly save forwarded outputs' instructions, it needs to
          // know the actual value forwarded outputs will be created with
          // Including operating costs prevents that
          let from_scanning = plans_from_scanning.contains(&plan.id());
          let to_use_operating_costs = if from_scanning { 0 } else { running_operating_costs };

          let PreparedSend { tx, post_fee_branches, mut operating_costs } =
            prepare_send(network, block_number, plan, to_use_operating_costs).await;
```

**File:** processor/src/multisigs/mod.rs (L778-794)
```rust
        if let Some((tx, eventuality)) = tx {
          // The main function we return to will send an event to the coordinator which must be
          // fired before these registered Eventualities have their Completions fired
          // Safety is derived from a mutable lock on the Scanner being preserved, preventing
          // scanning (and detection of Eventuality resolutions) before it's released
          // It's only released by the main function after it does what it will
          self
            .scanner
            .register_eventuality(key_bytes.as_ref(), block_number, id, eventuality.clone())
            .await;

          res.push((key, id, tx, eventuality));
        }

        // TODO: If the TX is None, restore its inputs to the scheduler for efficiency's sake
        // If this TODO is removed, also reduce the operating costs
      }
```

**File:** processor/src/networks/mod.rs (L442-455)
```rust
    let Some(tx_fee) = self.needed_fee(block_number, &inputs, &payments, &change).await? else {
      // This Plan is not fulfillable
      // TODO: Have Plan explicitly distinguish payments and branches in two separate Vecs?
      return Ok(PreparedSend {
        tx: None,
        // Have all of its branches dropped
        post_fee_branches: drop_branches(key, &payments),
        // This plan expects a change output valued at sum(inputs) - sum(outputs)
        // Since we can no longer create this change output, it becomes an operating cost
        // TODO: Look at input restoration to reduce this operating cost
        operating_costs: operating_costs +
          if change.is_some() { theoretical_change_amount } else { 0 },
      });
    };
```

**File:** processor/src/networks/bitcoin.rs (L446-458)
```rust
    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
      Ok(signable) => Ok(Some(signable)),
      Err(TransactionError::NoInputs) => {
        panic!("trying to create a bitcoin transaction without inputs")
      }
      // No outputs left and the change isn't worth enough/not even enough funds to pay the fee
      Err(TransactionError::NoOutputs | TransactionError::NotEnoughFunds { .. }) => Ok(None),
```
