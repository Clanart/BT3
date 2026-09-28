### Title
Unsolicited Bitcoin deposits to a retiring multisig are dropped and permanently locked - ([File: processor/src/multisigs/mod.rs](processor/src/multisigs/mod.rs))

### Summary
Serai’s Bitcoin integration distinguishes deposit outputs from internal branch, change, and forwarded outputs by using deterministic Taproot key offsets. An unprivileged user can send Bitcoin to the retiring multisig’s external, change, or forwarding address after rotation reaches `RotationStep::ClosingExisting`, and the processor explicitly filters those outputs out instead of forwarding or scheduling them. Because the retired threshold key is no longer expected to act afterward, these scanned funds become permanently unspendable.

### Finding Description
Bitcoin defines separate deterministic offsets for `External`, `Branch`, `Change`, and `Forwarded` outputs, all derived from the multisig group key and fixed hash-to-scalar labels. [1](#0-0)  `Bitcoin::get_outputs` scans every non-coinbase transaction, maps matching output scripts to the corresponding `OutputType`, and reports them to the multisig processor. [2](#0-1) 

During `ClosingExisting`, `Multisig::filter_outputs_due_to_closing` removes every `External` and `Forwarded` output, only retains a `Branch` output if its exact amount has a queued payment plan, and only retains a `Change` output if its transaction resolved a known plan with expected self-change. [3](#0-2)  The remaining outputs are then passed to the retiring scheduler, so donations removed by this filter never enter its UTXO set and never become inputs to a forwarding transaction. [4](#0-3) 

The same path separately skips `External` outputs sent to the existing key while forwarding or closing, under the assumption that valid deposits should have arrived earlier or should be sent to the new multisig. [5](#0-4)  However, Bitcoin permits anyone to send to an already known address, and the fixed internal addresses can be recomputed from the public group key. [6](#0-5)  Once the retirement block is acknowledged, the old scheduler is replaced and `RotationStep` returns to `UseExisting`, leaving no later path that schedules the retired key’s dropped UTXOs. [7](#0-6) 

### Impact Explanation
Any Bitcoin sent to a retiring multisig during the closing window can be permanently locked under its old threshold key. This includes unsolicited external-address deposits and outputs sent to the deterministic change or forwarding addresses that do not satisfy the narrow internal-output checks. The donations are visible to the scanner but are deliberately excluded from scheduler state, so no future plan can spend them.

### Likelihood Explanation
The attack only requires publishing an ordinary Bitcoin transaction to an address or script already exposed on-chain or computable from the public multisig key. Its timing requirement is limited to the closing phase of multisig rotation, but the sender does not need validator access, malformed data, or control over Serai. The donation itself is the trigger.

### Recommendation
Do not discard spendable outputs solely because they arrived during `ClosingExisting`. Track retired-key UTXOs until all detected outputs have been forwarded, or add a final sweep plan for any external/change/forwarded outputs not consumed by expected plans. At minimum, `filter_outputs_due_to_closing` should retain unexpected spendable outputs and forward them to the new multisig rather than treating them as nonexistent.

### Proof of Concept
1. Let `old_key` be the retiring Bitcoin multisig key and derive its external or change address through `external_address`, `branch_address`, `change_address`, or `forward_address`. [6](#0-5) 
2. During `RotationStep::ClosingExisting`, publish a transaction paying at least the dust threshold to the retiring key’s external or change script.
3. `Bitcoin::get_outputs` scans the transaction and emits a `ReceivedOutput`, classified from the script’s registered offset. [8](#0-7) 
4. The scanner accepts non-dust outputs and persists them for acknowledgement. [9](#0-8) 
5. `filter_outputs_due_to_closing` removes the unsolicited `External` output, and also removes an unexpected `Change` output unless it is associated with a known resolving plan. [3](#0-2) 
6. The retiring scheduler therefore never receives the UTXO, the retirement path deactivates the old multisig, and no subsequent scheduler can authorize spending that output. [7](#0-6)

### Citations

**File:** processor/src/networks/bitcoin.rs (L308-346)
```rust
const KEY_DST: &[u8] = b"Serai Bitcoin Output Offset";
static BRANCH_OFFSET: OnceLock<Scalar> = OnceLock::new();
static CHANGE_OFFSET: OnceLock<Scalar> = OnceLock::new();
static FORWARD_OFFSET: OnceLock<Scalar> = OnceLock::new();

// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

  (scanner, offsets, kinds)
```

**File:** processor/src/networks/bitcoin.rs (L661-674)
```rust
  fn branch_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Branch])))
  }

  fn change_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Change])))
  }

  fn forward_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Forwarded])))
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-699)
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
```

**File:** processor/src/multisigs/mod.rs (L484-549)
```rust
    let mut plans = vec![];
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
```

**File:** processor/src/multisigs/mod.rs (L608-617)
```rust
        let (is_retirement_block, outputs) = self.scanner.ack_block(txn, block_id.clone()).await;
        if is_retirement_block {
          let existing = self.existing.take().unwrap();
          assert!(existing.scheduler.empty());
          self.existing = self.new.take();
          *step = RotationStep::UseExisting;
          assert!(existing_payments.is_empty());
          existing_payments = new_payments;
          new_payments = vec![];
        }
```

**File:** processor/src/multisigs/mod.rs (L636-659)
```rust
    // If we're closing the existing multisig, filter its outputs down
    if *step == RotationStep::ClosingExisting {
      plans.extend(self.filter_outputs_due_to_closing(txn, &mut existing_outputs));
    }

    // Now that we've done all our filtering, schedule the existing multisig's outputs
    plans.extend({
      let existing = self.existing.as_mut().unwrap();
      let existing_key = existing.key;
      self.existing.as_mut().unwrap().scheduler.schedule::<D>(
        txn,
        existing_outputs,
        existing_payments,
        match *step {
          RotationStep::UseExisting => existing_key,
          RotationStep::NewAsChange |
          RotationStep::ForwardFromExisting |
          RotationStep::ClosingExisting => self.new.as_ref().unwrap().key,
        },
        match *step {
          RotationStep::UseExisting | RotationStep::NewAsChange => false,
          RotationStep::ForwardFromExisting | RotationStep::ClosingExisting => true,
        },
      )
```

**File:** processor/src/multisigs/mod.rs (L922-931)
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
```

**File:** processor/src/multisigs/scanner.rs (L559-566)
```rust
          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```
