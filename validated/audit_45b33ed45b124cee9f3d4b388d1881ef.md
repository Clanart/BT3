### Title
Permissionless deposit to a retiring multisig is silently dropped by `filter_outputs_due_to_closing`, permanently stranding funds - (File: processor/src/multisigs/mod.rs)

### Summary
Analogous to `EigenPod::verifyAndProcessWithdrawals` being callable by anyone — thereby bypassing the reporter-gated path that updates `delayedRewards` — Serai's Bitcoin pipeline lets any unprivileged party move funds on-chain in a way the account-updating path does not handle. During `RotationStep::ClosingExisting`, an `External` output paid to the still-active (retiring) multisig's deposit address is dropped from mint instructions **and** excluded from scheduler inputs, so the BTC is never forwarded to the new multisig and never credited. Once the set retires, the output is permanently stranded: the user deposit is neither spendable nor minted.

### Finding Description
`Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:686-740`) scans every non-coinbase transaction with `Scanner::scan_transaction`, which matches purely on `script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:199-214`). Anyone can therefore cause a `ReceivedOutput` to be reported simply by paying a registered Serai script — this is the permissionless "direct submission" path.

In `scanner_event_to_multisig_event` (`processor/src/multisigs/mod.rs:922-942`), when the step is `ForwardFromExisting` or `ClosingExisting` and the output's key is the *existing* multisig's key, the output is skipped for instruction emission with the comment "we'll report it once it hits the new multisig" — i.e., the accounting assumes a later forwarding step will produce the `InInstruction`.

However, `filter_outputs_due_to_closing` (`processor/src/multisigs/mod.rs:485-551`) only creates forwarding/branch plans for `OutputType::Branch` and (conditionally) `OutputType::Change`. `OutputType::External` and `OutputType::Forwarded` hit `=> false` and are removed from `existing_outputs` with **no plan created**, so the scheduler never receives them as inputs (`plans_from_block`, `mod.rs:636-660`). The forward-plan creation in the event handler is gated on `step == RotationStep::ForwardFromExisting` (`mod.rs:844-868`); in `ClosingExisting` no `PlanFromScanning::Forward` is ever produced for these outputs.

Net effect for a BTC deposit sent to the retiring multisig's external address during the `ClosingExisting` window:

- No `InInstruction` is emitted → no mint (accounting update skipped — the analog of `delayedRewards` never being updated).
- No `Plan` consumes the output → it is never forwarded.
- After `retire_set` completes, the retiring set's keys are dropped, so the UTXO can never be spent.

### Impact Explanation
An attacker (or an innocent re-user of an old deposit address, e.g., an exchange wallet with a cached Serai address) sends BTC to the retiring multisig's external address while `step == ClosingExisting`. The deposit is recognized by the scanner (it is a valid, spendable-looking P2TR output under Serai's key) but then falls through both processing paths: not minted, not forwarded, not refundable (`instruction_from_output` yields an instruction, yet the `continue` at `mod.rs:929-931` discards it before refund/instruction handling). The user's BTC is permanently locked in a retired-key UTXO and the corresponding sBTC is never issued — funds reported received that are not spendable, and accounting that silently diverges from custodied funds. Severity: Medium (requires the narrow rotation window, but is fully permissionless and irreversible).

### Likelihood Explanation
The deposit address is static public data; anyone can send to it at any time, including after rotation begins — exactly like anyone calling `verifyAndProcessWithdrawals` directly. Rotation windows (`ClosingExisting` lasting until the old set's scheduler drains) are recurring, protocol-defined periods. No validator collusion or key compromise is needed; a single ordinary Bitcoin transaction suffices.

### Recommendation
In `filter_outputs_due_to_closing`, do not drop `OutputType::External`/`Forwarded` outputs for the existing key: schedule a forwarding plan to the new multisig (mirroring the `shim_forward_plan` flow used in `ForwardFromExisting`), or emit a refund `PlanFromScanning`/`InInstruction` at block-ack time. Additionally, consider tracking outputs purely by on-chain observation (like the recommended EigenLayer-side `delayedWithdrawals` accounting) rather than assuming all relevant movements pass through the gated forwarding path.

### Proof of Concept
1. Wait for/observe `step == ClosingExisting` for multisig `K_old` (retirement block announced, new multisig `K_new` set).
2. Attacker broadcasts a Bitcoin tx paying `P` sats to `p2tr_script_buf(K_old)` — the external deposit script — with a valid `InInstruction` embedding.
3. `get_outputs` returns a `ReceivedOutput` with `kind == OutputType::External`, `key() == K_old`.
4. Event handler: `mod.rs:926-931` hits the `continue` → no instruction minted.
5. `plans_from_block` → `filter_outputs_due_to_closing`: `OutputType::External => false` → output removed, zero plans → never scheduled.
6. Set retires; keys deleted. The UTXO at the old tweaked key is unspendable and the deposit was never credited — permanently stranded funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** processor/src/multisigs/mod.rs (L485-551)
```rust
    existing_outputs.retain(|output| {
      match output.kind() {
        OutputType::External | OutputType::Forwarded => false,
        OutputType::Branch => {
          let scheduler = &mut self.existing.as_mut().unwrap().scheduler;
          // There *would* be a race condition here due to the fact we only mark a `Branch` output
          // as needed when we process the block (and handle scheduling), yet actual `Branch`
          // outputs may appear as soon as the next block (and we scan the next block before we
          // process the prior block)
          //
          // Unlike Eventuality checking, which happens on scanning and is therefore asynchronous,
          // all scheduling (and this check against the scheduler) happens on processing, which is
          // synchronous
          //
          // While we could move Eventuality checking into the block processing, removing its
          // asynchonicity, we could only check data the Scanner deems important. The Scanner won't
          // deem important Eventuality resolutions which don't create an output to Serai unless
          // it knows of the Eventuality. Accordingly, at best we could have a split role (the
          // Scanner noting completion of Eventualities which don't have relevant outputs, the
          // processing noting completion of ones which do)
          //
          // This is unnecessary, due to the current flow around Eventuality resolutions and the
          // current bounds naturally found being sufficiently amenable, yet notable for the future
          if scheduler.can_use_branch(output.balance()) {
            // We could simply call can_use_branch, yet it'd have an edge case where if we receive
            // two outputs for 100, and we could use one such output, we'd handle both.
            //
            // Individually schedule each output once confirming they're usable in order to avoid
            // this.
            let mut plan = scheduler.schedule::<D>(
              txn,
              vec![output.clone()],
              vec![],
              self.new.as_ref().unwrap().key,
              false,
            );
            assert_eq!(plan.len(), 1);
            let plan = plan.remove(0);
            plans.push(plan);
          }
          false
        }
        OutputType::Change => {
          // If the TX containing this output resolved an Eventuality...
          if let Some(plan) = ResolvedDb::get(txn, output.tx_id().as_ref()) {
            // And the Eventuality had change...
            // We need this check as Eventualities have a race condition and can't be relied
            // on, as extensively detailed above. Eventualities explicitly with change do have
            // a safe timing window however
            if PlanDb::plan_by_key_with_self_change::<N>(
              txn,
              // Pass the key so the DB checks the Plan's key is this multisig's, preventing a
              // potential issue where the new multisig creates a Plan with change *and a
              // payment to the existing multisig's change address*
              self.existing.as_ref().unwrap().key,
              plan,
            ) {
              // Then this is an honest change output we need to forward
              // (or it's a payment to the change address in the same transaction as an honest
              // change output, which is fine to let slip in)
              return true;
            }
          }
          false
        }
      }
    });
```

**File:** processor/src/multisigs/mod.rs (L922-942)
```rust
        for output in outputs {
          // If this is an External transaction to the existing multisig, and we're either solely
          // forwarding or closing the existing multisig, drop it
          // In the case of the forwarding case, we'll report it once it hits the new multisig
          if (match step {
            RotationStep::UseExisting | RotationStep::NewAsChange => false,
            RotationStep::ForwardFromExisting | RotationStep::ClosingExisting => true,
          }) && (output.key() == self.existing.as_ref().unwrap().key)
          {
            continue;
          }

          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
            continue;
          };
```

**File:** processor/src/networks/bitcoin.rs (L686-740)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }

      if outputs.is_empty() {
        continue;
      }

      // populate the outputs with the origin and data
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
    }

    outputs
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```
