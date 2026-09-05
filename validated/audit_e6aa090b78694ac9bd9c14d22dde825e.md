### Title
Stale mempool entries retained after replace-by-fee/replace-across-fork enable duplicate (address, nonce) rows and admissibility divergence - (File: `stackslib/src/core/mempool.rs`)

### Summary
`MemPoolDB::try_add_tx` determines whether an incoming transaction should replace an existing mempool entry that shares the same origin/sponsor `(address, nonce)` pair, but the actual database write only ever executes an `INSERT` (`INSERT OR REPLACE INTO mempool (txid, ...)`) keyed by `txid`. Because the new transaction almost always has a different `txid` than the transaction it is supposed to replace, the old row is never physically deleted from the `mempool` table — only an event is broadcast to external subscribers claiming it was dropped. This produces the exact bug class described in the external report: two persisted mempool entries sharing the same index key (`origin_address`/`origin_nonce` here, analogous to `fromBlock` in the reference finding), which makes downstream index-based lookups and ranking imprecise or manipulable.

### Finding Description
`try_add_tx` looks up any transaction that already occupies the same `origin_nonce` or `sponsor_nonce` slot via `get_tx_metadata_by_address` [1](#0-0) , then decides whether the incoming transaction may **replace-by-fee** or **replace-across-fork**: [2](#0-1) 

Once `add_tx` is `true`, the function only performs a single `INSERT OR REPLACE INTO mempool (txid, origin_address, origin_nonce, ...)` statement parameterized by the **new** transaction's `txid`, and then simply fires an event claiming the prior transaction was dropped — there is no corresponding `DELETE FROM mempool WHERE txid = ?` (or any other statement) that removes the row belonging to `prior_tx.txid`: [3](#0-2) 

Because SQLite's `INSERT OR REPLACE` only resolves conflicts on the table's unique/primary key (`txid`), and the new transaction's `txid` differs from `prior_tx.txid` whenever the fee, signature, or other fields changed, the old row keyed by `prior_tx.txid` survives in the `mempool` table alongside the newly inserted row. Both rows carry the same `origin_address`/`origin_nonce` (and possibly the same `sponsor_address`/`sponsor_nonce`), i.e., the table now has two entries with the same "index" the way `UserLock`/`TotalLock` in the reference report could have two entries with the same `fromBlock`.

This directly poisons every subsequent nonce-indexed query:
- `get_tx_metadata_by_address` (`SELECT * FROM mempool WHERE {origin|sponsor}_address = ?1 AND {origin|sponsor}_nonce = ?2`) can now match either row nondeterministically, undermining the very replace-by-fee/replace-across-fork logic that depends on it being a 1:1 mapping [4](#0-3) .
- The block-candidate selection query used during mempool iteration explicitly partitions and ranks candidates `PARTITION BY origin_address ORDER BY origin_nonce ASC, sort_fee_rate DESC` and joins against the current on-chain `nonces` table to filter to `m.origin_nonce = ns.nonce` [5](#0-4) . With two persisted rows sharing the same `origin_nonce`, both entries satisfy the nonce filter and both receive low `origin_rank` values, allowing the (stale, fee-losing, or cross-fork) prior transaction to be reconsidered as a valid candidate for block inclusion long after it should have been superseded/dropped.

### Impact Explanation
This breaks the intended one-to-one mapping between an account's nonce and a single admissible mempool transaction. A stale, "replaced" transaction can remain selectable by the mempool's block-candidate ranking query even though the node's own replace-by-fee/replace-across-fork logic determined it should no longer be authoritative. This is a mempool-versus-block admissibility divergence: the effective state the mempool ranks/serves for that nonce is ambiguous and depends on iteration order/tie-breaking rather than the deterministic "highest fee, most canonical fork" rule the code intends to enforce, which is exactly the class of impact (mempool-vs-block admissibility divergence, an incorrect notion of which transaction is charged for a given nonce) called out as High severity in this scan's rules.

### Likelihood Explanation
This triggers on the ordinary, expected replace-by-fee and replace-across-fork code paths — no attacker privilege beyond normal transaction submission is required, and the condition (new `txid` differing from the superseded transaction's `txid`) is the overwhelmingly common case, since `txid` is derived from the full signed transaction bytes (fee, signature, etc.) which necessarily changes between the original and its replacement.

### Recommendation
In `try_add_tx`, when `prior_tx` is `Some` and it is being replaced, explicitly issue `tx.execute("DELETE FROM mempool WHERE txid = ?1", params![prior_tx.txid])` (or perform the insert plus delete in a single transaction) before/along with the `INSERT`, so that only one mempool row can ever exist per `(origin_address, origin_nonce)` pair, matching the invariant the replace-by-fee/replace-across-fork logic assumes.

### Proof of Concept
1. Submit transaction `T1` from address `A` with `nonce=5`, `fee=100`. `try_add_tx` inserts a row keyed by `txid(T1)`.
2. Submit transaction `T2` from the same address `A`, same `nonce=5`, `fee=200` (a legitimate replace-by-fee bump). `get_tx_metadata_by_address` finds `T1` as `prior_tx`; since `200 > 100`, `add_tx = true`, `replace_reason = REPLACE_BY_FEE`.
3. The code executes `INSERT OR REPLACE INTO mempool (txid=txid(T2), origin_nonce=5, ...)`. Because `txid(T2) != txid(T1)`, this is a fresh row, not a replacement of `T1`'s row. No `DELETE` for `txid(T1)` is executed anywhere in the function [3](#0-2) .
4. The `mempool` table now contains both `T1` and `T2`, each with `origin_address=A, origin_nonce=5`.
5. A later call to `get_tx_metadata_by_address(conn, true, &A, 5)` [4](#0-3)  or the block-candidate ranking query [5](#0-4)  can return/rank either `T1` or `T2`, even though the node already decided `T2` should supersede `T1`.

### Citations

**File:** stackslib/src/core/mempool.rs (L1704-1726)
```rust
                LEFT JOIN nonces AS ns ON m.sponsor_address = ns.address
                WHERE (no.address IS NULL OR m.origin_nonce = no.nonce)
                    AND (ns.address IS NULL OR m.sponsor_nonce = ns.nonce)
                    AND m.txid NOT IN (SELECT txid FROM considered_txs)
                ORDER BY accept_time ASC
                LIMIT 11650 -- max transactions that can fit in one block
            ),
            address_nonce_ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY origin_address
                        ORDER BY origin_nonce ASC, sort_fee_rate DESC
                    ) AS origin_rank,
                    ROW_NUMBER() OVER (
                        PARTITION BY sponsor_address
                        ORDER BY sponsor_nonce ASC, sort_fee_rate DESC
                    ) AS sponsor_rank
                FROM nonce_filtered
            )
            SELECT txid, origin_nonce, origin_address, sponsor_nonce, sponsor_address, fee_rate
            FROM address_nonce_ranked
            ORDER BY origin_rank ASC, sponsor_rank ASC, sort_fee_rate DESC
            ";
```

**File:** stackslib/src/core/mempool.rs (L2075-2087)
```rust
    pub fn get_tx_metadata_by_address(
        conn: &DBConn,
        is_origin: bool,
        addr: &StacksAddress,
        nonce: u64,
    ) -> Result<Option<MemPoolTxMetadata>, db_error> {
        let sql = format!(
            "SELECT * FROM mempool WHERE {0}_address = ?1 AND {0}_nonce = ?2",
            if is_origin { "origin" } else { "sponsor" }
        );
        let args = params![addr.to_string(), u64_to_sql(nonce)?];
        query_row(conn, &sql, args)
    }
```

**File:** stackslib/src/core/mempool.rs (L2185-2196)
```rust
        // do we already have txs with either the same origin nonce or sponsor nonce ?
        let prior_tx = {
            match MemPoolDB::get_tx_metadata_by_address(tx, true, origin_address, origin_nonce)? {
                Some(prior_tx) => Some(prior_tx),
                None => MemPoolDB::get_tx_metadata_by_address(
                    tx,
                    false,
                    sponsor_address,
                    sponsor_nonce,
                )?,
            }
        };
```

**File:** stackslib/src/core/mempool.rs (L2198-2244)
```rust
        let mut replace_reason = MemPoolDropReason::REPLACE_BY_FEE;

        // if so, is this a replace-by-fee? or a replace-in-chain-tip?
        let add_tx = if let Some(ref prior_tx) = prior_tx {
            if tx_fee > prior_tx.tx_fee {
                // is this a replace-by-fee ?
                debug!(
                    "Can replace {} with {} for {},{} by fee ({} < {})",
                    &prior_tx.txid, &txid, origin_address, origin_nonce, &prior_tx.tx_fee, &tx_fee
                );
                replace_reason = MemPoolDropReason::REPLACE_BY_FEE;
                true
            } else if !MemPoolDB::are_blocks_in_same_fork(
                chainstate,
                &prior_tx.tenure_consensus_hash,
                &prior_tx.tenure_block_header_hash,
                &consensus_hash,
                &block_header_hash,
            )? {
                // is this a replace-across-fork ?
                debug!(
                    "Can replace {} with {} for {},{} across fork",
                    &prior_tx.txid, &txid, origin_address, origin_nonce
                );
                replace_reason = MemPoolDropReason::REPLACE_ACROSS_FORK;
                true
            } else {
                // there's a >= fee tx in this fork, cannot add
                info!("TX conflicts with sponsor/origin nonce in same fork with >= fee";
                      "new_txid" => %txid,
                      "old_txid" => %prior_tx.txid,
                      "origin_addr" => %origin_address,
                      "origin_nonce" => origin_nonce,
                      "sponsor_addr" => %sponsor_address,
                      "sponsor_nonce" => sponsor_nonce,
                      "new_fee" => tx_fee,
                      "old_fee" => prior_tx.tx_fee);
                false
            }
        } else {
            // no conflicting TX with this origin/sponsor, go ahead and add
            true
        };

        if !add_tx {
            return Err(MemPoolRejection::ConflictingNonceInMempool);
        }
```

**File:** stackslib/src/core/mempool.rs (L2246-2293)
```rust
        tx.update_bloom_counter(coinbase_height, txid, prior_tx.as_ref().map(|tx| &tx.txid))?;

        let sql = "INSERT OR REPLACE INTO mempool (
            txid,
            origin_address,
            origin_nonce,
            sponsor_address,
            sponsor_nonce,
            tx_fee,
            length,
            consensus_hash,
            block_header_hash,
            height,
            accept_time,
            tx)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)";

        let args = params![
            txid,
            origin_address.to_string(),
            u64_to_sql(origin_nonce)?,
            sponsor_address.to_string(),
            u64_to_sql(sponsor_nonce)?,
            u64_to_sql(tx_fee)?,
            u64_to_sql(length)?,
            consensus_hash,
            block_header_hash,
            u64_to_sql(coinbase_height)?,
            u64_to_sql(get_epoch_time_secs())?,
            tx_bytes,
        ];

        tx.execute(sql, args)
            .map_err(|e| MemPoolRejection::DBError(db_error::SqliteError(e)))?;

        tx.update_mempool_pager(txid)?;

        // broadcast drop event if a tx is being replaced
        if let (Some(prior_tx), Some(event_observer)) = (prior_tx, event_observer) {
            event_observer.mempool_txs_dropped(
                vec![prior_tx.txid],
                Some(txid.clone()),
                replace_reason,
            );
        };

        Ok(())
    }
```
