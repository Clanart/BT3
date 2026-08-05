This confirms fees are only deducted upon commit/execution (via `Bank::commit_transactions` → `CommittedTransaction.fee_payer_post_balance`), never for transactions that just sit in the buffer and expire out. That's sufficient to write up the finding.

### Title
Unbounded low-cost priority-fee eviction of legitimate transactions from the leader's bounded scheduler buffer - (File: `core/src/banking_stage/transaction_scheduler/transaction_state_container.rs`)

### Summary
`TransactionStateContainer` is a fixed-capacity, priority-ordered "book" of pending transactions maintained by the leader's transaction scheduler [1](#0-0) . When the container is full, `push_ids_into_queue` unconditionally evicts the *lowest-priority* transaction to make room for any incoming transaction with higher priority [2](#0-1) . Priority is derived purely from the declared compute-unit price / fee of the still-unexecuted transaction, not from any guarantee that the transaction will ever land [3](#0-2) . Because Solana transaction fees are only charged upon `Bank::commit_transactions` (i.e., only if the transaction is actually scheduled and executed) [4](#0-3) , an unprivileged sender can flood the buffer with many high-priority, short-lived transactions from disposable, minimally-funded fee-payer accounts that are designed to never be committed (e.g., they will be starved out by per-slot CU limits, or will simply blockhash-expire before being scheduled). Each such transaction evicts a genuine lower-fee user transaction from the bounded queue at essentially zero cost, exactly mirroring the CLOB `_executeBidLimitOrder` "evict worst order" bug: a bounded, priced, eviction-based structure that can be gamed by unprivileged, cheap, ephemeral entries.

### Finding Description
The scheduler buffer models pending transactions as `TransactionPriorityId { priority, id }` stored in a `BTreeSet`, ordered by priority [5](#0-4) . On receipt, `TransactionViewReceiveAndBuffer::handle_packet_batch_message` performs only local, cheap checks before insertion: sanitization, blockhash-age validity, and `Consumer::check_fee_payer_unlocked` (which validates the fee payer merely has enough lamports to cover the *fee*, not the full transaction) [6](#0-5) . It then unconditionally pushes the transaction into the priority queue via `push_ids_into_queue`, which drops the current lowest-priority buffered transaction(s) whenever the map exceeds `capacity` [2](#0-1) .

None of these checks verify that the incoming transaction will actually be scheduled/executed before its blockhash expires. The scheduler only periodically evicts *already-buffered* stale transactions via `incremental_recheck` when the node is not leader, and transactions that time out or never make it into a bank are simply dropped, without any fee being collected — fees are only realized through `Bank::commit_transactions`/`create_commit_results`, which requires the transaction to have actually been processed by a bank [7](#0-6) .

This reproduces the CLOB bug's exact broken invariant: a size-bounded, priority-ordered structure ("book") that evicts the worst existing entry to admit a better-priced new one, where "better-priced" carries no obligation to actually be fulfilled/settled, and where the attacker can trivially multiply the number of admissible entries by using many disposable identities (many fee-payer keypairs) to bypass any per-sender limiting, just as the report's attacker used "separate accounts to bypass the maximum number of limits per transaction."

### Impact Explanation
An unprivileged network participant can repeatedly evict legitimate, fee-paying users' transactions from a leader's mempool during that leader's slot(s), at negligible sustained cost (only minimal per-account lamports to pass the fee-payer-balance check, no actual fee is paid for transactions that never land). This causes wrongful non-inclusion/censorship of legitimate transactions — a fairness/availability degradation of block production reachable purely through the normal transaction-submission path (TPU), not through RPC, malicious peer, or validator-privilege assumptions, fitting the "non-RPC remote exhaustion/degradation" impact category.

### Likelihood Explanation
The attack requires no special privileges, no validator/peer trust, and only ordinary transaction construction: many funded-just-enough fee-payer keypairs, each submitting a transaction with a high `set_compute_unit_price` and a valid-but-soon-unusable blockhash-window. It can be repeated continuously and cheaply. The main friction is that priority is computed from `reward / cost`, and CU price competition scales with the actual amounts other users are willing to pay, but the same friction exists in the original CLOB report (attacker must outbid legitimate orders) and does not prevent the bounded eviction primitive from being triggered.

### Recommendation
Do not let unexecuted (never-committed) high-priority transactions permanently and repeatedly displace already-buffered lower-priority ones purely on declared priority. Consider: (1) requiring a minimum bonded/escrowed cost proportional to priority claimed at admission time so eviction has real cost to the submitter even if the transaction never lands; (2) rate-limiting or Sybil-resisting insertions per distinct fee-payer/IP at the QUIC/TPU layer independent of priority so many disposable accounts cannot cheaply repeat the eviction; (3) tracking a fee-payer's historical "non-landed high-priority churn" and deprioritizing/penalizing accounts whose high-priority transactions systematically fail to land.

### Proof of Concept
1. Fund N disposable keypairs with just enough lamports to pass `check_fee_payer_unlocked` for a transaction with `set_compute_unit_price(HIGH)`.
2. From each keypair, submit a transaction with a valid recent blockhash and a very high compute-unit price, targeting accounts/loads that make it unlikely to be scheduled before blockhash expiry (e.g., intentionally exceeding target CU budget via many low-value transfers, or simply submitting far more such transactions than `max_scanned_transactions_per_scheduling_pass`/block CU budget can consume in the window).
3. Observe via `push_ids_into_queue`'s return value (`num_dropped_on_capacity` in `ReceivingStats`) that legitimate lower-priority buffered transactions are evicted [2](#0-1) .
4. Repeat with new disposable keypairs before the previous batch's blockhashes expire; confirm none of the attacker's transactions are ever committed (`fee_details.total_fee() == 0` / no `CommittedTransaction`), meaning the attacker pays no fees for the eviction achieved.

### Citations

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L41-49)
```rust
/// The container maintains a fixed capacity. If the queue is full when pushing
/// a new transaction, the lowest priority transaction will be dropped.
pub(crate) struct TransactionStateContainer<Tx: TransactionWithMeta> {
    capacity: usize,
    priority_queue: BTreeSet<TransactionPriorityId>,
    id_to_transaction_state: Slab<TransactionState<Tx>>,
    held_transactions: Vec<TransactionPriorityId>,
    nonces_in_use: HashMap<Pubkey, TransactionPriorityId, PubkeyHasherBuilder>,
}
```

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L178-201)
```rust
    fn push_ids_into_queue(
        &mut self,
        priority_ids: impl Iterator<Item = TransactionPriorityId>,
    ) -> usize {
        for id in priority_ids {
            self.priority_queue.insert(id);
        }

        // The number of items in the `id_to_transaction_state` map is
        // greater than or equal to the number of elements in the queue.
        // To avoid the map going over capacity, we use the length of the
        // map here instead of the queue.
        let num_dropped = self
            .id_to_transaction_state
            .len()
            .saturating_sub(self.capacity);

        for _ in 0..num_dropped {
            let priority_id = self.priority_queue.pop_first().expect("queue is not empty");
            self.remove_state(priority_id.id);
        }

        num_dropped
    }
```

**File:** core/src/transaction_priority.rs (L32-65)
```rust
pub(crate) fn calculate_priority_and_cost<Tx: TransactionMeta + SVMStaticMessage>(
    bank: &Bank,
    transaction: &Tx,
    transaction_configuration: &TransactionConfiguration,
) -> (u64, u64) {
    let cost = CostModel::calculate_cost_for_executed_transaction(
        transaction,
        u64::from(transaction_configuration.compute_unit_limit),
        transaction_configuration.loaded_accounts_data_size_limit,
        &bank.feature_set,
    )
    .sum();
    let fee_details = solana_fee::calculate_fee_details(
        transaction,
        bank.fee_structure().lamports_per_signature,
        transaction_configuration.priority_fee_lamports,
        bank.fee_features(),
    );
    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    // We need a multiplier here to avoid rounding down too aggressively.
    // For many transactions, the cost will be greater than the fees in terms of raw lamports.
    // For the purposes of calculating prioritization, we multiply the fees by a large number so that
    // the cost is a small fraction.
    // An offset of 1 is used in the denominator to explicitly avoid division by zero.
    const MULTIPLIER: u64 = 1_000_000;
    (
        reward
            .saturating_mul(MULTIPLIER)
            .saturating_div(cost.saturating_add(1)),
        cost,
    )
```

**File:** svm/src/transaction_commit_result.rs (L41-46)
```rust
    fn was_fee_paying(&self) -> bool {
        match self {
            Ok(committed_tx) => committed_tx.fee_details.total_fee() > 0,
            Err(_) => false,
        }
    }
```

**File:** core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs (L1-14)
```rust
use crate::banking_stage::scheduler_messages::TransactionId;

/// A unique identifier tied with priority ordering for a transaction/packet:
#[derive(Copy, Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct TransactionPriorityId {
    pub(crate) priority: u64,
    pub(crate) id: TransactionId,
}

impl TransactionPriorityId {
    pub(crate) fn new(priority: u64, id: TransactionId) -> Self {
        Self { priority, id }
    }
}
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L312-340)
```rust
            // Check blockhash transaction age is ok, or nonce transaction has a valid nonce.
            // Only a fully validated nonce address can be used for priority queue eviction.
            let validated_nonce_address = match working_bank.check_transaction_without_status_cache(
                state.transaction(),
                working_bank.max_processing_age(),
                &mut error_counters,
            ) {
                // Valid nonce transaction
                Ok(Some(nonce_address)) => Some(nonce_address),

                // Valid blockhash transaction
                Ok(None) => None,

                // Invalid
                Err(ref err) => {
                    receiving_stats.add_transaction_error(err);
                    continue;
                }
            };

            // Check the transaction's fee-payer validates.
            if let Err(_err) = Consumer::check_fee_payer_unlocked(
                working_bank,
                state.transaction(),
                &mut error_counters,
            ) {
                receiving_stats.num_dropped_on_fee_payer += 1;
                continue;
            };
```

**File:** runtime/src/bank.rs (L4451-4526)
```rust
    fn create_commit_results(
        processing_results: Vec<TransactionProcessingResult>,
    ) -> Vec<TransactionCommitResult> {
        processing_results
            .into_iter()
            .map(|processing_result| {
                let processing_result = processing_result?;
                let executed_units = processing_result.executed_units();
                let loaded_accounts_data_size = processing_result.loaded_accounts_data_size();

                match processing_result {
                    ProcessedTransaction::Executed(executed_tx) => {
                        let successful = executed_tx.was_successful();
                        let execution_details = executed_tx.execution_details;
                        let LoadedTransaction {
                            accounts: loaded_accounts,
                            fee_details,
                            rollback_accounts,
                            ..
                        } = executed_tx.loaded_transaction;

                        // Rollback value is used for failure.
                        let fee_payer_post_balance = if successful {
                            loaded_accounts[0].1.lamports()
                        } else {
                            rollback_accounts.fee_payer().1.lamports()
                        };

                        Ok(CommittedTransaction {
                            status: execution_details.status,
                            log_messages: execution_details.log_messages,
                            inner_instructions: execution_details.inner_instructions,
                            return_data: execution_details.return_data,
                            executed_units,
                            fee_details,
                            loaded_account_stats: TransactionLoadedAccountsStats {
                                loaded_accounts_count: loaded_accounts.len(),
                                loaded_accounts_data_size,
                            },
                            fee_payer_post_balance,
                        })
                    }
                    ProcessedTransaction::FeesOnly(fees_only_tx) => Ok(CommittedTransaction {
                        status: Err(fees_only_tx.load_error),
                        log_messages: None,
                        inner_instructions: None,
                        return_data: None,
                        executed_units,
                        fee_details: fees_only_tx.fee_details,
                        loaded_account_stats: TransactionLoadedAccountsStats {
                            loaded_accounts_count: fees_only_tx.rollback_accounts.count(),
                            loaded_accounts_data_size,
                        },
                        fee_payer_post_balance: fees_only_tx
                            .rollback_accounts
                            .fee_payer()
                            .1
                            .lamports(),
                    }),
                    ProcessedTransaction::NoOp(no_op_tx) => Ok(CommittedTransaction {
                        status: Err(no_op_tx.validation_error),
                        log_messages: None,
                        inner_instructions: None,
                        return_data: None,
                        executed_units,
                        fee_details: FeeDetails::default(),
                        loaded_account_stats: TransactionLoadedAccountsStats {
                            loaded_accounts_count: 0,
                            loaded_accounts_data_size,
                        },
                        fee_payer_post_balance: no_op_tx.fee_payer_balance.unwrap_or(0),
                    }),
                }
            })
            .collect()
    }
```
