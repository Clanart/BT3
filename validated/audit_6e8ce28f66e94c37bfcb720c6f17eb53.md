## Title
Unauthenticated durable-nonce transactions can evict/censor legitimate nonce transactions in the banking-stage mempool - (File: `runtime/src/bank/check_transactions.rs`)

## Summary
The Symm IO bug allowed an unprivileged party (PartyB) to permanently block PartyA's legitimate, signature-bound transaction by cheaply incrementing a nonce value that the pending transaction's signature depended on, without ever needing PartyA's authorization. The Agave analog is in the leader-side transaction age/validity check used to admit transactions into the banking-stage buffer: it deliberately skips the durable-nonce **authority** check (`strict_nonce_authority_check = false`), so an attacker who is *not* the nonce authority can still get a transaction "recognized" as a valid pending transaction for a victim's nonce account, and that fake transaction can evict the victim's real, correctly-signed nonce transaction from the mempool via the nonce-dedup eviction logic — even though the fake transaction can never actually succeed on-chain.

## Finding Description
`check_age_and_compute_budget_limits` (used by `check_transactions`/`check_transactions_with_processed_slots`, the leader-side path that decides whether a transaction is buffered) calls `check_transaction_age` with the authority check explicitly disabled: [1](#0-0) 

That flows into `check_nonce_transaction_validity`, where the block that verifies the signer actually matches the nonce account's stored `authority` is entirely skipped when `strict_nonce_authority_check` is `false`: [2](#0-1) 

Because the nonce account's stored durable nonce hash and authority are public account data, an attacker can:
1. Read any victim's nonce account (`nonce_pubkey`) and its current durable-nonce hash.
2. Build a transaction whose first instruction is `AdvanceNonceAccount(nonce_pubkey, attacker_pubkey)` — naming *themselves*, not the real authority, as the "authority" argument — signed only by themselves, using the victim's nonce hash as `recent_blockhash`.
3. Submit it with a high priority fee.

This transaction is *not* actually valid: real execution-time validation in the SVM correctly checks the authority and would reject it: [3](#0-2) 

But the leader-side `check_nonce_transaction_validity` (with `strict_nonce_authority_check=false`) does not perform that authority check, so it still returns `Some(nonce_address)` for the attacker's bogus transaction, causing it to be treated as a legitimate "nonce transaction" keyed on the victim's `nonce_address` for buffering/dedup purposes.

The scheduler's receive/buffer path deduplicates by nonce address and evicts a lower-priority buffered transaction for the same nonce address in favor of a higher-priority incoming one, as shown by existing test coverage of that exact mechanism: [4](#0-3) 

Because the attacker's spoofed-authority transaction is accepted by the age check (not dropped), it is eligible to participate in this eviction logic and can knock the victim's real, correctly-signed nonce transaction out of the buffer/queue — exactly mirroring the "increment PartyA's/PartyB's nonce with no cost to block their pending signature-bound transaction" primitive from the Symm IO report.

## Impact Explanation
This is a non-RPC, unprivileged remote censorship/DoS primitive against any dApp or user relying on durable nonce transactions (common for offline-signed transactions, e.g., exchanges, hardware wallets, cross-program flows). An attacker with no relationship to the nonce account (no authority key) can repeatedly craft cheap transactions referencing a victim's nonce account to evict/replace the victim's legitimate transaction in leader mempools, indefinitely delaying or blocking its inclusion — a false-non-acceptance / transaction-censorship impact at the banking-stage/TPU layer, matching the "unprivileged ... transactions/CPI ... non-RPC remote exhaustion" category.

## Likelihood Explanation
The prerequisite information (nonce account pubkey and its current stored durable-nonce hash) is fully public on-chain, and the only cost to the attacker is fee-bidding for priority, since the malicious transaction never needs to execute successfully — it only needs to be admitted past the loosened age check to participate in dedup eviction. No trusted role, leaked key, or malicious-validator assumption is required; this is achievable by any ordinary transaction sender.

## Recommendation
Enforce (or at least attempt to verify) the nonce authority match in the leader-side age/dedup check path as well, or ensure the nonce-dedup eviction logic re-validates the authority signer before allowing a transaction to occupy/evict the dedup slot for a given nonce address, so that spoofed-authority transactions cannot compete for or evict legitimately-signed nonce transactions.

## Proof of Concept
1. Identify victim's durable nonce account `N` (owner=System Program) and read its current `authority` A and `durable_nonce` hash `H` — both public.
2. Craft transaction `T_attack`: instruction 0 = `advance_nonce_account(N, attacker_pubkey)`, `recent_blockhash = H`, signed only by attacker (attacker is a valid "signer" per message but not equal to `A`), with a high `compute_unit_price`.
3. Submit `T_attack` to the leader. `check_age_and_compute_budget_limits` → `check_transaction_age` → `check_nonce_transaction_validity` is invoked with `strict_nonce_authority_check=false` [1](#0-0) , so `T_attack` is accepted as a valid pending transaction keyed to `N`, regardless of the authority mismatch [5](#0-4) .
4. The victim's real, correctly-signed nonce transaction for `N` (lower priority fee) sitting in the buffer is evicted per the nonce-dedup eviction behavior [4](#0-3) .
5. `T_attack` itself will ultimately fail at execution time (`validate_transaction_nonce` correctly rejects the bad authority) [3](#0-2) , but the victim's legitimate transaction has already been displaced, forcing them to resubmit/rebroadcast — repeatable indefinitely by the attacker at negligible cost.

### Citations

**File:** runtime/src/bank/check_transactions.rs (L209-217)
```rust
                    let nonce_address = self.check_transaction_age(
                        tx.borrow(),
                        max_age,
                        &next_durable_nonce,
                        &hash_queue,
                        error_counters,
                        strict_nonce_size_check,
                        false,
                    )?;
```

**File:** runtime/src/bank/check_transactions.rs (L258-284)
```rust
    pub(super) fn check_nonce_transaction_validity(
        &self,
        message: &impl SVMMessage,
        next_durable_nonce: &DurableNonce,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> Option<(Pubkey, u64)> {
        let nonce_is_advanceable = message.recent_blockhash() != next_durable_nonce.as_hash();
        if !nonce_is_advanceable {
            return None;
        }

        let (nonce_address, nonce_data) =
            self.load_message_nonce_data(message, strict_nonce_size_check)?;

        if strict_nonce_authority_check
            && !message
                .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
                .any(|signer| signer == &nonce_data.authority)
        {
            return None;
        }

        let previous_lamports_per_signature = nonce_data.get_lamports_per_signature();

        Some((nonce_address, previous_lamports_per_signature))
    }
```

**File:** svm/src/transaction_processor.rs (L871-892)
```rust
        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L1428-1454)
```rust
    // a higher priority incoming nonce transaction evicts the existing transaction,
    // a lower or equal priority incoming nonce transaction is dropped
    #[test_case(HIGH_FEE, LOW_FEE; "hilo_drop")]
    #[test_case(HIGH_FEE, HIGH_FEE; "hihi_drop")]
    #[test_case(LOW_FEE, HIGH_FEE; "lohi_evict")]
    fn test_receive_and_buffer_nonce_dedup_drop_evict(old_fee: u64, new_fee: u64) {
        let (sender, receiver) = bounded(1024);
        let (bank_forks, mint_keypair) = test_bank_forks_with_fee();
        let (mut receive_and_buffer, mut container) =
            setup_transaction_view_receive_and_buffer(receiver, bank_forks.clone());
        let (nonce_pubkey, durable) = create_nonce_identity(&bank_forks, &mint_keypair.pubkey());
        let new_has_priority = new_fee > old_fee;

        send_transactions(
            &sender,
            &[create_nonce_transaction(
                &mint_keypair,
                &nonce_pubkey,
                old_fee,
                durable,
            )],
        );
        assert_eq!(
            receive(&mut receive_and_buffer, &mut container).num_buffered,
            1
        );
        let prior_nonce_entry = *container
```
