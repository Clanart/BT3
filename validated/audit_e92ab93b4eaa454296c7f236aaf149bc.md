### Title
Unconditional Citrea LCP Fetch in `handle_finalized_payout` Blocks Kickoff Transaction Sending When Citrea Light Client Prover Is Unavailable — (`core/src/operator.rs`, `core/src/task/payout_checker.rs`)

---

### Summary

`Operator::handle_finalized_payout` calls `fetch_validate_and_store_lcp` unconditionally **before** the kickoff transaction is added to the send queue. If the Citrea light client prover is unavailable or has not yet produced the LCP for the payout block, this call returns an error, the entire database transaction is rolled back, and the kickoff transaction is never queued. The `PayoutCheckerTask` repeats the same pattern. Because the LCP is only needed if the operator is later *challenged* (in `send_asserts`), its fetch is a preparatory step that should not gate the kickoff. If Citrea is unavailable for a period approaching the challenge window, the operator cannot begin the reimbursement flow and risks losing the funds they fronted for the withdrawal.

---

### Finding Description

**Root cause — `handle_finalized_payout`**

Inside the branch that handles a kickoff that is not yet on-chain, the code fetches and saves the LCP *before* pushing the kickoff transaction to `txs_to_send`:

```rust
// core/src/operator.rs  (inside handle_finalized_payout, "kickoff not on chain" branch)
let _ = self
    .citrea_client
    .fetch_validate_and_store_lcp(
        payout_block_height as u64,
        citrea_idx as u32,
        &self.db,
        dbtx.as_deref_mut(),
        self.config.protocol_paramset(),
    )
    .await?;          // ← propagates error; kickoff never queued if this fails

// ...
txs_to_send.push(kickoff_tx.clone());   // ← never reached on failure
``` [1](#0-0) 

`fetch_validate_and_store_lcp` itself has **no retry loop**: if `get_light_client_proof` returns `None` (proof not yet produced by Citrea) it immediately returns an error:

```rust
// core/src/citrea.rs
let lcp_result = self
    .get_light_client_proof(payout_block_height, paramset)
    .await?;
let (_lcp, lcp_receipt, _l2_height) = match lcp_result {
    Some(lcp) => lcp,
    None => {
        return Err(eyre::eyre!(
            "Light client proof could not be fetched found for block height {}",
            payout_block_height
        ).into())
    }
};
``` [2](#0-1) 

**Root cause — `PayoutCheckerTask`**

`PayoutCheckerTask::run_once` calls `handle_finalized_payout` (which queues the kickoff inside the same DB transaction) and then calls `fetch_validate_and_store_lcp` a second time. Both calls propagate errors with `?`. If either fails, `dbtx.commit()` is never reached, the entire transaction is rolled back, and the kickoff is not persisted in the tx-sender queue:

```rust
// core/src/task/payout_checker.rs
let kickoff_txid = self.operator
    .handle_finalized_payout(&mut dbtx, ...)
    .await?;                                    // queues kickoff inside dbtx

let _ = self.operator.citrea_client
    .fetch_validate_and_store_lcp(...)
    .await?;                                    // rolls back dbtx on failure

// ...
dbtx.commit().await?;                           // never reached
``` [3](#0-2) 

The LCP is only consumed in `send_asserts`, which is triggered only when the operator is actually challenged:

```rust
// core/src/operator.rs  (send_asserts)
let lcp_receipt = self
    .citrea_client
    .fetch_validate_and_store_lcp(
        payout_block_height as u64,
        deposit_idx as u32,
        ...
    )
    .await?;
``` [4](#0-3) 

The LCP is therefore a **preparatory** artifact, not a prerequisite for sending the kickoff.

---

### Impact Explanation

An operator that has fronted a withdrawal (paid the user from its own collateral) must send a kickoff transaction to begin the on-chain reimbursement flow. If the Citrea light client prover is unavailable — even transiently — when `PayoutCheckerTask` first processes the payout, the kickoff is never queued. The task retries every 60 seconds in production, so a short outage causes only a delay. However, if Citrea is unavailable for a period that approaches the challenge window (the time the operator has to complete the reimbursement before collateral can be slashed), the operator cannot recover its fronted funds. This constitutes a potential permanent loss of operator collateral and reimbursement outputs — both within the allowed impact scope.

---

### Likelihood Explanation

Citrea's light client prover produces proofs with a non-zero delay after each Bitcoin block. During normal operation the LCP for a given block may not be available immediately, causing the first several `PayoutCheckerTask` iterations to fail. Any extended Citrea outage (network partition, prover bug, upgrade downtime) extends this window. The condition requires no privileged action and no attacker: it is triggered by the normal operational state of an external system that the bridge already depends on.

---

### Recommendation

Decouple the LCP fetch from the kickoff dispatch. The kickoff transaction should be queued and committed regardless of whether the LCP is available. The LCP fetch should be attempted separately (e.g., in a dedicated retry loop or lazily inside `send_asserts`). Concretely:

1. In `handle_finalized_payout`, remove the `fetch_validate_and_store_lcp` call (or wrap it in a non-fatal error handler that logs and continues).
2. In `PayoutCheckerTask::run_once`, remove the `fetch_validate_and_store_lcp` call (or move it outside the DB transaction so a failure does not roll back the committed kickoff).
3. Ensure `send_asserts` already fetches the LCP when actually needed (it does — `core/src/operator.rs` lines 1315–1324), so no safety regression occurs.

---

### Proof of Concept

1. Operator fronts a withdrawal; `unhandled_payouts` row is created in the DB.
2. Citrea light client prover is stopped (or the LCP for the payout block is not yet available).
3. `PayoutCheckerTask::run_once` fires:
   - `handle_finalized_payout` succeeds and adds the kickoff tx to the tx-sender queue **within `dbtx`**.
   - `fetch_validate_and_store_lcp` calls `get_light_client_proof` → returns `None` → returns `Err(...)`.
   - `?` propagates; `dbtx.commit()` is skipped; the entire transaction rolls back.
   - The kickoff tx is **not** in the tx-sender queue.
4. Task retries every 60 s; each attempt rolls back for the same reason.
5. If Citrea remains unavailable until the challenge window expires, the operator's collateral is at risk and the reimbursement output is permanently lost.

### Citations

**File:** core/src/operator.rs (L1315-1324)
```rust
        let lcp_receipt = self
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                deposit_idx as u32,
                &self.db,
                Some(&mut dbtx),
                self.config.protocol_paramset(),
            )
            .await?;
```

**File:** core/src/operator.rs (L1826-1842)
```rust
                    let _ = self
                        .citrea_client
                        .fetch_validate_and_store_lcp(
                            payout_block_height as u64,
                            citrea_idx as u32,
                            &self.db,
                            dbtx.as_deref_mut(),
                            self.config.protocol_paramset(),
                        )
                        .await?;

                    // sanity check
                    if kickoff_tx.1.compute_txid() != kickoff_txid {
                        return Err(eyre::eyre!("Kickoff txid mismatch for deposit outpoint: {}, kickoff txid: {:?}, computed txid: {:?}",
                        deposit_data.get_deposit_outpoint(), kickoff_txid, kickoff_tx.1.compute_txid()).into());
                    }
                    txs_to_send.push(kickoff_tx.clone());
```

**File:** core/src/citrea.rs (L342-354)
```rust
        let lcp_result = self
            .get_light_client_proof(payout_block_height, paramset)
            .await?;
        let (_lcp, lcp_receipt, _l2_height) = match lcp_result {
            Some(lcp) => lcp,
            None => {
                return Err(eyre::eyre!(
                    "Light client proof could not be fetched found for block height {}",
                    payout_block_height
                )
                .into())
            }
        };
```

**File:** core/src/task/payout_checker.rs (L72-108)
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

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;
```
