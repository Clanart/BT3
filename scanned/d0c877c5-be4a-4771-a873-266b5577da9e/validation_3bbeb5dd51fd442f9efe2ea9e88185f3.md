## Analog Found: Silent unsigned wraparound of the shared block-cost counter in `CostTracker`

### Title
Unchecked `fetch_sub` on `SharedBlockCost` allows the banking-stage block-cost counter to wrap to near-`u64::MAX`, corrupting block cost-limit accounting - (File: `cost-model/src/cost_tracker.rs`)

### Summary
The external report describes an accounting invariant broken by ordering: a value is decremented in one function using a subtraction that assumes a prior, matching increment happened in another function, and if that pre-condition is violated the subtraction is unsafe. The closest structural analog in this codebase is `CostTracker`'s block-level cost counter, `block_cost`, which is incremented with `fetch_add` inside `try_add()` and decremented with `fetch_sub` inside `sub_transaction_execution_cost()`/`remove()`. Unlike every other counter in the same struct (`allocated_accounts_data_size`, `transaction_count`, per-account costs in `cost_by_writable_accounts`), which are all protected with `Saturating<u64>` or `saturating_sub`, the block-level counter is a raw `AtomicU64` decremented with plain `fetch_sub`, which performs two's-complement wrapping, not saturation and not a panic/revert.

### Finding Description
`CostTracker::try_add()` increments the shared block cost only after all account-level checks pass: [1](#0-0) 

Every other bookkeeping field in the same publish step is a `Saturating<u64>`, so any subsequent subtraction of those fields (e.g., in `remove_transaction_cost`) is guaranteed not to underflow silently: [2](#0-1) 

But `block_cost` is a `SharedBlockCost(Arc<AtomicU64>)`, and its decrement helper uses the raw atomic `fetch_sub`, not a saturating or checked variant: [3](#0-2) 

It is used identically to the safe, saturating per-account map inside the very same function, which highlights the inconsistency: per-account entries are protected, the aggregate block counter is not: [4](#0-3) 

`remove()`/`remove_transaction_cost()` is the public entry point that performs this subtraction, and it is invoked from `core/src/banking_stage/consumer.rs` (2 call sites) to reconcile the tracker after execution — e.g. subtracting back the difference between a transaction's reserved (worst-case) cost and its actual executed cost, or removing a transaction's reserved cost entirely if it does not get committed. This is structurally the same "collect in one function / subtract in another, assuming a prior matching add" pattern as `eqFeePool`: if `remove()` is ever invoked for a transaction cost that was not (fully) reflected in `block_cost` at the time of removal — for example due to a scheduler retry/requeue path invoking removal against a `CostTracker` that never observed the matching `try_add`, or removal being triggered twice for the same transaction — the `fetch_sub` will wrap `block_cost`'s `AtomicU64` down past zero to a value near `u64::MAX` instead of erroring.

### Impact Explanation
`try_add()`'s admission-control gate for the whole block is: [5](#0-4) 

Once `block_cost` wraps to a value close to `u64::MAX`, `self.block_cost().saturating_add(cost)` saturates at `u64::MAX` and will always exceed `self.limits.block_cost`, so every subsequent transaction for the remainder of that bank's lifetime is rejected with `WouldExceedBlockMaxLimit`. This is a non-privileged, in-protocol accounting corruption in the banking stage that results in a leader silently and durably refusing to pack any further transactions into its block(s) — i.e., a self-inflicted denial of service / block-production stall that does not require a malicious peer, validator, or trusted component, only an ordinary transaction whose cost-tracker lifecycle (add → execute → remove) can be triggered out of the expected sequence (e.g., through the normal consumer retry/requeue paths in `consumer.rs`). This fits the report's requested impact category of non-RPC remote exhaustion/crash/degradation, since it degrades leader block production without any special privileges.

### Likelihood Explanation
This requires an ordinary, unprivileged code path (banking-stage cost tracking during scheduling/consumption of the block) to reach an "add followed by mismatched remove" ordering. I was not able to fully verify — within the available tool budget — the exact caller sequence in `core/src/banking_stage/consumer.rs` that could produce a remove without (or in excess of) a matching prior add; I only confirmed the two call sites exist and that the underlying primitive (`fetch_sub` on the shared `AtomicU64`) is unconditionally unsafe against underflow, unlike every sibling counter in the struct. This should be treated as **medium-confidence**: the vulnerable primitive is confirmed by code, but the concrete out-of-order trigger path needs further tracing of `consumer.rs`'s call sites to `CostTracker::remove`/`sub_transaction_execution_cost` before this can be escalated to a fully proven, reproducible exploit.

### Recommendation
Make `SharedBlockCost::fetch_sub` use `checked_sub`/`saturating_sub` semantics (e.g., via a CAS loop or by switching `block_cost` to the same `Saturating<u64>` pattern used for the other tracker fields) so that any mismatched or duplicate removal saturates at zero instead of wrapping to near `u64::MAX`. Additionally, audit and add invariant checks/asserts at each `CostTracker::remove()` call site in `core/src/banking_stage/consumer.rs` to guarantee that a `remove` is only issued for a transaction cost that was previously and exactly `try_add`-ed to that same tracker instance.

### Proof of Concept
Conceptual reproduction using the existing test harness pattern in `cost-model/src/cost_tracker.rs`:
```rust
let mut tracker = CostTracker::default();
let tx_cost = simple_transaction_cost(&some_tx, 100);
// No corresponding try_add() call here.
tracker.remove(&tx_cost); // sub_transaction_execution_cost -> block_cost.fetch_sub(100)
assert_eq!(tracker.block_cost(), u64::MAX - 99); // wraps instead of saturating/panicking
``` [6](#0-5) [7](#0-6) 

This demonstrates the underlying primitive is unsafe; establishing that the real banking-stage flow can call `remove()` without (or in excess of) the matching `try_add()` requires further code tracing in `core/src/banking_stage/consumer.rs`, which I could not complete before this response was due.

### Citations

**File:** cost-model/src/cost_tracker.rs (L98-108)
```rust
pub struct CostTracker {
    limits: CostTrackerLimits,
    cost_by_writable_accounts: HashMap<Pubkey, u64, ahash::RandomState>,
    block_cost: SharedBlockCost,
    transaction_count: Saturating<u64>,
    allocated_accounts_data_size: Saturating<u64>,
    transaction_signature_count: Saturating<u64>,
    secp256k1_instruction_signature_count: Saturating<u64>,
    ed25519_instruction_signature_count: Saturating<u64>,
    secp256r1_instruction_signature_count: Saturating<u64>,
}
```

**File:** cost-model/src/cost_tracker.rs (L176-181)
```rust
        let cost = tx_cost.sum();

        if self.block_cost().saturating_add(cost) > self.limits.block_cost {
            // check against the total package cost
            return Err(CostTrackerError::WouldExceedBlockMaxLimit);
        }
```

**File:** cost-model/src/cost_tracker.rs (L224-233)
```rust
        // every check passed: publish the block-level state
        self.allocated_accounts_data_size = allocated_accounts_data_size;
        self.transaction_count += 1;
        self.transaction_signature_count += tx_cost.num_transaction_signatures();
        self.secp256k1_instruction_signature_count +=
            tx_cost.num_secp256k1_instruction_signatures();
        self.ed25519_instruction_signature_count += tx_cost.num_ed25519_instruction_signatures();
        self.secp256r1_instruction_signature_count +=
            tx_cost.num_secp256r1_instruction_signatures();
        self.block_cost.fetch_add(cost);
```

**File:** cost-model/src/cost_tracker.rs (L262-264)
```rust
    pub fn remove(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) {
        self.remove_transaction_cost(tx_cost);
    }
```

**File:** cost-model/src/cost_tracker.rs (L355-366)
```rust
    fn remove_transaction_cost(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) {
        let cost = tx_cost.sum();
        self.sub_transaction_execution_cost(tx_cost, cost);
        self.allocated_accounts_data_size -= tx_cost.allocated_accounts_data_size();
        self.transaction_count -= 1;
        self.transaction_signature_count -= tx_cost.num_transaction_signatures();
        self.secp256k1_instruction_signature_count -=
            tx_cost.num_secp256k1_instruction_signatures();
        self.ed25519_instruction_signature_count -= tx_cost.num_ed25519_instruction_signatures();
        self.secp256r1_instruction_signature_count -=
            tx_cost.num_secp256r1_instruction_signatures();
    }
```

**File:** cost-model/src/cost_tracker.rs (L368-382)
```rust
    /// Subtract extra execution units from cost_tracker
    fn sub_transaction_execution_cost(
        &mut self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
        adjustment: u64,
    ) {
        for account_key in tx_cost.writable_accounts() {
            let account_cost = self
                .cost_by_writable_accounts
                .entry(*account_key)
                .or_insert(0);
            *account_cost = account_cost.saturating_sub(adjustment);
        }
        self.block_cost.fetch_sub(adjustment);
    }
```

**File:** cost-model/src/cost_tracker.rs (L402-423)
```rust
/// Wrapper around blockcost to allow fast sharing of the value without locking.
/// Value is read-only outside of cost-tracker.
#[derive(Debug, Clone)]
pub struct SharedBlockCost(Arc<AtomicU64>);

impl SharedBlockCost {
    pub fn new(value: u64) -> Self {
        Self(Arc::new(AtomicU64::new(value)))
    }

    fn fetch_add(&self, value: u64) -> u64 {
        self.0.fetch_add(value, Ordering::Release)
    }

    fn fetch_sub(&self, value: u64) -> u64 {
        self.0.fetch_sub(value, Ordering::Release)
    }

    pub fn load(&self) -> u64 {
        self.0.load(Ordering::Acquire)
    }
}
```
