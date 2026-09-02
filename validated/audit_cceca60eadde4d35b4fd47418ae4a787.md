### Title
Unvalidated `deposit_outpoint` in `send_move_to_vault_tx` allows mismatch between recorded deposit and broadcast move-to-vault transaction - (File: `core/src/rpc/aggregator.rs`)

### Summary
`ClementineAggregator::send_move_to_vault_tx()` accepts a caller-supplied `deposit_outpoint` and a raw `movetx`, validates the movetx's output amounts/scriptpubkeys against the protocol parameters, but never checks that `movetx.input[0].previous_output` actually equals the supplied `deposit_outpoint` before persisting `TxMetadata { deposit_outpoint: Some(deposit_outpoint), ..., tx_type: TransactionType::MoveToVault }` and queuing the transaction for broadcast.

### Finding Description
In `send_move_to_vault_tx` [1](#0-0) , the code:
1. Deserializes the attacker-supplied `raw_tx` into `movetx`.
2. Validates only structural/value properties of `movetx` (one input, two outputs, `bridge_amount`/anchor amounts, and the N-of-N + security-council scriptpubkey).
3. Never compares `movetx.input[0].previous_output` (the actual UTXO being spent) against the `deposit_outpoint` parameter supplied in the same request.

It then persists the mapping [2](#0-1)  associating the caller-chosen `deposit_outpoint` with this `movetx` in `TxMetadata`, which is later used by `get_deposit_data_with_move_tx` (referenced from `core/src/rpc/aggregator.rs` in the optimistic-payout flow) to look up the deposit associated with a given move txid, and by watchtower/payout logic that keys reimbursement/optimistic-payout eligibility off this `deposit_outpoint ↔ move_txid` binding.

Because the movetx must still carry a valid N-of-N (verifier musig2) signature to be spendable/broadcastable, an attacker cannot fabricate an arbitrary move-to-vault transaction for funds they don't control. However, this endpoint lets *any caller able to reach the aggregator RPC* submit a **previously and validly produced** movetx (obtained honestly for deposit A through the normal `new_deposit` flow) together with an **arbitrary, mismatched `deposit_outpoint`** (deposit B), since there is no assertion `movetx.input[0].previous_output == deposit_outpoint`. The system will store deposit B → move_txid(A) in its bookkeeping.

### Impact Explanation
This breaks the equality "a `deposit_outpoint` committed in the bridge's deposit-to-move-tx binding" vs "the actual outpoint the on-chain move transaction spends." If downstream logic (deposit lookup, reimbursement eligibility, optimistic-payout matching) trusts the metadata's `deposit_outpoint` field rather than re-deriving it from the broadcast transaction's own input, this could let an attacker link a genuine move-to-vault transaction to a deposit outpoint that was never actually moved (or was already claimed elsewhere), corrupting the deposit-to-move-tx bookkeeping that underlies reimbursement/withdrawal authorization. I could not fully trace, within budget, whether any consumer of `TxMetadata.deposit_outpoint` (vs. the transaction's own committed input) is actually used to authorize a payout or reimbursement — this is the key uncertainty. If such a consumer exists and trusts the stored `deposit_outpoint` without cross-checking the transaction's real input, this would allow an unauthorized/misattributed reimbursement or a permanently-unmatched honest deposit (never resolved to a move UTXO), which would map to the Critical/High impact categories in scope. I was not able to confirm this consumption path with certainty in the remaining budget.

### Likelihood Explanation
Reaching this code path requires only being able to call the aggregator's `send_move_to_vault_tx` RPC with a syntactically valid, previously-produced (already N-of-N-signed) move-to-vault transaction plus an arbitrary `deposit_outpoint` value — no privileged role, key compromise, or verifier/operator collusion is needed beyond obtaining any one legitimately-signed movetx (which any depositor obtains through the normal deposit flow). The missing check is a single-line omission (`movetx.input[0].previous_output == deposit_outpoint`), making the vulnerable condition simple to trigger if the RPC is reachable by unprivileged callers.

### Recommendation
In `send_move_to_vault_tx`, before persisting `TxMetadata`, assert that `movetx.input[0].previous_output == deposit_outpoint`, returning `Status::invalid_argument` on mismatch, mirroring the existing output/scriptpubkey validation at [3](#0-2) .

### Proof of Concept
1. Attacker (or any depositor) legitimately drives the normal deposit flow for deposit outpoint `A`, obtaining a fully N-of-N-signed `movetx_A` via `new_deposit`/`create_movetx` (see `core/src/rpc/aggregator.rs:810-849`).
2. Attacker calls `send_move_to_vault_tx` with `raw_tx = movetx_A` but `deposit_outpoint = B` (a different, unrelated deposit outpoint the attacker also knows about, e.g. one belonging to another user that hasn't been moved yet).
3. The handler's checks at `core/src/rpc/aggregator.rs:2019-2073` pass (they only check `movetx_A`'s own outputs/values), and the DB records `deposit_outpoint = B` associated with `movetx_A`'s txid at `core/src/rpc/aggregator.rs:2075-2095`, even though `movetx_A` actually spends outpoint `A`, not `B`. [1](#0-0) [2](#0-1)

### Citations

**File:** core/src/rpc/aggregator.rs (L1998-2036)
```rust
            let request = request.into_inner();
            let movetx: bitcoin::Transaction = bitcoin::consensus::deserialize(
                &request
                    .raw_tx
                    .ok_or_eyre("raw_tx is required")
                    .map_to_status()?
                    .raw_tx,
            )
            .wrap_err("Failed to deserialize movetx")
            .map_to_status()?;
            let deposit_outpoint: bitcoin::OutPoint = request
                .deposit_outpoint
                .ok_or(Status::invalid_argument("deposit_outpoint is required"))?
                .try_into()?;

            tracing::info!(
                "Parsed send move to vault tx rpc params, deposit outpoint: {:?}, movetx hex: {}",
                deposit_outpoint,
                bitcoin::consensus::encode::serialize_hex(&movetx)
            );

            // check if transaction is a movetx
            if movetx.input.len() != 1 || movetx.output.len() != 2 {
                return Err(Status::invalid_argument(
                    "Transaction is not a movetx, input or output lengths are not correct",
                ));
            }
            // check output values
            // movetx always has 0 sat anchor output
            if !(movetx.output[0].value == self.config.protocol_paramset().bridge_amount
                && movetx.output[1].value == Amount::from_sat(0))
            {
                return Err(Status::invalid_argument(format!(
                    "Transaction is not a movetx, output sat values are not correct, should be ({}, 0), got ({}, {})",
                    self.config.protocol_paramset().bridge_amount,
                    movetx.output[0].value,
                    movetx.output[1].value,
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L2075-2095)
```rust
            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    Some(TxMetadata {
                        deposit_outpoint: Some(deposit_outpoint),
                        operator_xonly_pk: None,
                        round_idx: None,
                        kickoff_idx: None,
                        tx_type: TransactionType::MoveToVault,
                    }),
                    &movetx,
                    FeePayingType::CPFP,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
```
