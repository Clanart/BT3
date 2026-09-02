### Title
Non-unique `withdrawals.move_to_vault_txid` after replacement causes `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` to attribute reimbursement to the wrong operator - (File: core/src/database/verifier.rs)

### Summary
`update_replacement_deposit_move_txid` performs a bare `UPDATE withdrawals SET move_to_vault_txid = $2 WHERE idx = $1` with no check that `$2` is not already used by another `idx`, and the schema's only uniqueness guarantee is on `idx` (`ON CONFLICT (idx)` in `upsert_move_to_vault_txid_from_citrea_deposit`). Once two `withdrawals` rows share the same `move_to_vault_txid`, `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id`'s `INNER JOIN deposits d ON d.move_to_vault_txid = w.move_to_vault_txid` can match more than one `withdrawals` row, and since the query uses `fetch_one`, an arbitrary (possibly wrong) row's `payout_payer_operator_xonly_pk` and `kickoff_txid` is returned for `deposit_id`.

### Finding Description
Binding claimed: `attribution(deposit_id) == operator_who_funded(deposit_id)`, established by `payout_payer_operator_xonly_pk` returned for a given `deposit_id`.

Code path:
- `withdrawals` rows are indexed by `idx`, initially inserted/kept unique per `idx` via `upsert_move_to_vault_txid_from_citrea_deposit` (`ON CONFLICT (idx)`), [1](#0-0) .
- `update_replacement_deposit_move_txid` blindly rewrites `move_to_vault_txid` for one `idx` with no uniqueness check against other rows' `move_to_vault_txid` values [2](#0-1) .
- `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` joins `deposits.move_to_vault_txid` to `withdrawals.move_to_vault_txid` and calls `fetch_one`, which silently picks one of possibly several matching rows if the value is no longer unique [3](#0-2) .
- Similarly, `get_payout_info_from_move_txid` looks up by `move_to_vault_txid` alone and returns `idx` from whichever row's `payout_txid`/`payout_tx_blockhash` happen to be non-null, which can also be the wrong `idx` once collision occurs [4](#0-3) .

Root cause: the schema/query layer treats `move_to_vault_txid` as if it were a stable 1:1 key to `idx`, but only `idx` is actually protected from duplication; after a replacement update, nothing prevents two rows from converging on the same `move_to_vault_txid` value, and the lookup functions have no `idx`-scoping or `ORDER BY`/uniqueness assertion to disambiguate.

### Impact Explanation
If exploited, the reimbursement authority (`payout_payer_operator_xonly_pk`) and `kickoff_txid` credited to one `deposit_id` can be swapped with that of a different, unrelated `deposit_id`/`idx`, causing an operator to be reimbursed for a withdrawal it never funded, or an honest operator that did front funds to be denied reimbursement for its `deposit_id`. This matches the Critical impact category "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
I could not fully verify, given available tool budget, (a) the exact caller/trigger conditions for `update_replacement_deposit_move_txid` in `core/src/verifier.rs`, i.e., whether the unprivileged attacker described in the threat model (someone who can only broadcast Bitcoin transactions, deposit, call `withdraw`, and hit the aggregator's gRPC) can actually cause a `new_move_txid` value to be written that collides with another row's existing `move_to_vault_txid`; and (b) whether `deposits.move_to_vault_txid` is updated in lockstep with the `withdrawals` row during a "replacement" event (which is required for the collision to actually manifest through the specific join in `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` rather than simply causing a lookup miss). Without confirming that the replacement path is reachable by an unprivileged actor with attacker-controlled collision inputs, this remains a database/query-layer robustness gap whose exploitability by the defined threat model is unconfirmed.

### Recommendation
Add a partial/composite uniqueness constraint (e.g., unique index on `move_to_vault_txid` where non-null, or unique on `(move_to_vault_txid)` enforced at the DB level) so `update_replacement_deposit_move_txid` fails atomically on collision; additionally, scope `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` and `get_payout_info_from_move_txid` to join/filter on `idx` in addition to `move_to_vault_txid`, and replace `fetch_one` with a query that asserts exactly one row is returned (erroring otherwise) to fail loudly instead of silently attributing an arbitrary row.

### Proof of Concept
```rust
// cargo test in core/src/database/verifier.rs (or a new test module)
// 1. Create withdrawals row idx1 with move_to_vault_txid = txid_A via
//    upsert_move_to_vault_txid_from_citrea_deposit(idx1, txid_A)
//    and set payout info via update_payout_txs_and_payer_operator_xonly_pk(idx1, txid_A, Some(pk1), blockhash1)
// 2. Create withdrawals row idx2 with move_to_vault_txid = txid_B via
//    upsert_move_to_vault_txid_from_citrea_deposit(idx2, txid_B)
//    and set payout info via update_payout_txs_and_payer_operator_xonly_pk(idx2, txid_B, Some(pk2), blockhash2)
// 3. Insert deposits rows: deposits[d1].move_to_vault_txid = txid_A, deposits[d2].move_to_vault_txid = txid_B
// 4. Call update_replacement_deposit_move_txid(idx2, txid_A) -> now withdrawals[idx2].move_to_vault_txid == txid_A == withdrawals[idx1].move_to_vault_txid
// 5. Call get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(d1's deposit_id)
//    assert result.payout_payer_operator_xonly_pk is deterministically pk1 (idx1's operator) — 
//    demonstrate it can instead return pk2/idx2 data (order-dependent, since fetch_one has no ORDER BY
//    and two rows now match d1.move_to_vault_txid == txid_A)
// This violates ATTRIBUTION: attribution(d1) != operator_who_funded(d1) when the wrong row is picked.
```

### Citations

**File:** core/src/database/verifier.rs (L56-63)
```rust
        let query = sqlx::query(
            "INSERT INTO withdrawals (idx, move_to_vault_txid)
             VALUES ($1, $2)
             ON CONFLICT (idx) DO UPDATE
             SET move_to_vault_txid = $2",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(*move_to_vault_txid));
```

**File:** core/src/database/verifier.rs (L85-106)
```rust
    pub async fn update_replacement_deposit_move_txid(
        &self,
        tx: DatabaseTransaction<'_>,
        idx: u32,
        new_move_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET move_to_vault_txid = $2
             WHERE idx = $1
             RETURNING idx",
        )
        .bind(i32::try_from(idx).wrap_err("Failed to convert idx to i32")?)
        .bind(TxidDB(new_move_txid))
        .fetch_optional(tx.deref_mut())
        .await?;

        if query.is_none() {
            return Err(eyre::eyre!("Replacement move txid not found: {}", idx).into());
        }
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L253-280)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
                ))
            })
            .transpose()
    }
```

**File:** core/src/database/verifier.rs (L315-346)
```rust
    pub async fn get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(Option<XOnlyPublicKey>, Option<BlockHash>, Option<Txid>), BridgeError> {
        let query = sqlx::query_as::<
            _,
            (
                Option<XOnlyPublicKeyDB>,
                Option<BlockHashDB>,
                Option<TxidDB>,
            ),
        >(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.kickoff_txid
             FROM withdrawals w
             INNER JOIN deposits d ON d.move_to_vault_txid = w.move_to_vault_txid
             WHERE d.deposit_id = $1",
        )
        .bind(i32::try_from(deposit_id).wrap_err("Failed to convert deposit id to i32")?);

        let result: (
            Option<XOnlyPublicKeyDB>,
            Option<BlockHashDB>,
            Option<TxidDB>,
        ) = execute_query_with_tx!(self.connection, tx, query, fetch_one)?;

        Ok((
            result.0.map(|pk| pk.0),
            result.1.map(|block_hash| block_hash.0),
            result.2.map(|txid| txid.0),
        ))
    }
```
