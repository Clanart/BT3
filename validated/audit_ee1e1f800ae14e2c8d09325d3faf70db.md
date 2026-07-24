The code path is real and the double `end_round` call is confirmed. Let me trace it precisely.

**`run_once` in `payout_checker.rs`:** [1](#0-0) 

1. Calls `handle_finalized_payout(&mut dbtx, ...)` at line 74.
2. Then unconditionally calls `self.operator.end_round(&mut dbtx)` at line 102 (under `#[cfg(feature = "automation")]`).

**`handle_finalized_payout` in `operator.rs`:** [2](#0-1) 

Under `#[cfg(feature = "automation")]`, if `current_round_index != round_idx`, it calls `self.end_round(dbtx)` at line 883 — the **first** call.

**`end_round` in `operator.rs`:** [3](#0-2) 

Each call reads `current_round_index` from the DB (within the shared transaction), advances it, queues `BurnUnusedKickoffConnectors`, `ReadyToReimburse`, and the next round tx, then writes the incremented index back.

---

**The double-call sequence (both under `automation` feature):**

```
run_once
  └─ handle_finalized_payout(&mut dbtx)
       └─ [current_round_index N-1 != round_idx N]
            └─ end_round(&mut dbtx)   ← FIRST CALL
                 reads  current_round_index = N-1
                 writes current_round_index = N
                 queues BurnUnused(N-1), ReadyToReimburse(N-1), Round(N) tx
  └─ end_round(&mut dbtx)             ← SECOND CALL (unconditional, line 102)
       reads  current_round_index = N   (already advanced inside same dbtx)
       writes current_round_index = N+1
       queues BurnUnused(N), ReadyToReimburse(N), Round(N+1) tx
```

Because both calls share the same `dbtx`, the second call sees the already-incremented index and advances it again.

**Concrete effects of the second spurious call:**

- `current_round_index` is written as N+1 instead of N — the operator's round state is permanently skipped by one.
- `BurnUnusedKickoffConnectors` for round N is queued immediately, burning every kickoff connector in round N that was not used in this single payout. Any deposit whose reimbursement was assigned to one of those connectors can never be claimed.
- `ReadyToReimburse` for round N is queued before round N's kickoff tx is even confirmed on-chain, creating a dependency ordering violation.
- Round N+1 tx is queued prematurely; the operator now believes it is in round N+1 and will assign future deposits to round N+1 connectors, leaving round N connectors permanently burned and inaccessible.

**Trigger condition:** This fires whenever `get_unused_and_signed_kickoff_connector` returns a connector in round N while `current_round_index` is N-1 — i.e., whenever the current round's connectors are exhausted. This is a normal operational state, not an exotic one.

**Unprivileged entry:** An unprivileged user can accelerate this by making enough deposits to exhaust round N-1's connectors, then triggering a payout. The `PayoutCheckerTask` runs automatically and requires no privileged action.

---

### Title
Double `end_round` call in `PayoutCheckerTask::run_once` corrupts `current_round_index` and burns reimbursement UTXOs — (`core/src/task/payout_checker.rs`)

### Summary
When the `automation` feature is enabled and a payout's kickoff connector is in round N while `current_round_index` is N-1, `handle_finalized_payout` calls `end_round` internally (line 883 of `operator.rs`), and then `run_once` calls `end_round` a second time unconditionally (line 102 of `payout_checker.rs`). Both calls share the same database transaction, so the second call sees the already-advanced index and increments it again, skipping round N entirely.

### Finding Description
`PayoutCheckerTask::run_once` calls `self.operator.handle_finalized_payout(...)` and then, under `#[cfg(feature = "automation")]`, unconditionally calls `self.operator.end_round(&mut dbtx)`. [4](#0-3) 

`handle_finalized_payout` itself, also under `#[cfg(feature = "automation")]`, calls `self.end_round(dbtx)` when `current_round_index != round_idx`. [5](#0-4) 

`end_round` reads `current_round_index` at entry, performs all round-transition work, and writes the incremented index — all within the passed transaction. [6](#0-5) 

Because both calls use the same `dbtx`, the second call observes the index already written by the first and advances it a second time.

### Impact Explanation
- `current_round_index` is written as N+1 instead of N, permanently corrupting the operator's round state.
- `BurnUnusedKickoffConnectors` for round N is queued immediately, destroying every unused kickoff connector in round N. Deposits assigned to those connectors lose their reimbursement path — operator collateral for those deposits is permanently stranded.
- `ReadyToReimburse` for round N and the Round N+1 tx are queued out of sequence, creating on-chain dependency violations that can cause further tx failures.

### Likelihood Explanation
The trigger condition — `current_round_index` being one behind the round of the first available kickoff connector — is a normal operational state that occurs every time a round's connectors are exhausted. No special attacker capability is required; a user making enough deposits to fill a round and then receiving a payout is sufficient.

### Recommendation
Remove the unconditional `end_round` call from `run_once` (line 102 of `payout_checker.rs`). The call inside `handle_finalized_payout` already handles the case where a round transition is needed before the kickoff. Alternatively, remove the `end_round` call from `handle_finalized_payout` and rely solely on the one in `run_once`, but add a guard so it is only called when `handle_finalized_payout` did not already advance the round.

### Proof of Concept
Set up a mock DB with `current_round_index = Round(0)` and a deposit whose kickoff connector is in `Round(1)`. Call `run_once`. Assert that `end_round` is called exactly once and `current_round_index` is `Round(1)`, not `Round(2)`. With the current code, `current_round_index` will be `Round(2)` and `BurnUnusedKickoffConnectors` for `Round(1)` will have been queued, demonstrating the double-advance and premature burn.

### Citations

**File:** core/src/task/payout_checker.rs (L72-102)
```rust
        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;
```

**File:** core/src/operator.rs (L862-884)
```rust
        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }
```

**File:** core/src/operator.rs (L1021-1043)
```rust
    pub async fn end_round<'a>(
        &'a self,
        mut dbtx: DatabaseTransaction<'a>,
    ) -> Result<(), BridgeError> {
        // get current round index
        let current_round_index = self.db.get_current_round_index(Some(&mut dbtx)).await?;

        let mut activation_prerequisites = Vec::new();

        let operator_winternitz_public_keys = self
            .db
            .get_operator_kickoff_winternitz_public_keys(None, self.signer.xonly_public_key)
            .await?;
        let kickoff_wpks = KickoffWinternitzKeys::new(
            operator_winternitz_public_keys,
            self.config.protocol_paramset().num_kickoffs_per_round,
            self.config.protocol_paramset().num_round_txs,
        )?;

        // if we are at round 0, which is just the collateral, we need to start the first round
        if current_round_index == RoundIndex::Collateral {
            return self.start_first_round(dbtx, kickoff_wpks).await;
        }
```
