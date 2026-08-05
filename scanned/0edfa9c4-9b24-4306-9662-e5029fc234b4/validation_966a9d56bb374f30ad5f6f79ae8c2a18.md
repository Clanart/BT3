## Title
Durable nonce authority rotation does not invalidate stale pre-signed transactions - the durable nonce value survives `AuthorizeNonceAccount`, so previously discarded, still-valid-looking transactions become executable again if the authority is ever reverted (`programs/system/src/system_instruction.rs`, `svm/src/transaction_processor.rs`)

### Summary
The Dharma report's root cause is that a mutable, revertible "signing key" (global key / recovery key) is not bound to a monotonically increasing nonce, so a transaction that becomes invalid the moment the key changes can become valid again if the key is ever reverted to its old value. Agave's durable-nonce mechanism has the structurally identical property: the value that gates whether a durable-nonce transaction is executable — the stored `durable_nonce` hash — is advanced **only** by `AdvanceNonceAccount`. `AuthorizeNonceAccount` changes the `authority` field of the nonce account but leaves `durable_nonce` untouched. As a result, a durable-nonce transaction that was rejected/discarded solely because the nonce authority temporarily changed becomes executable again if the authority is later reverted back to the original signer, exactly as in the Dharma "key reuse" scenario.

### Finding Description
`advance_nonce_account` is the only path that changes `data.durable_nonce`, and it explicitly requires the *current* authority's signature: [1](#0-0) 

`authorize_nonce_account`, by contrast, only updates the `authority` field via `Versions::authorize`; the `durable_nonce` and `fee_calculator` fields are preserved unchanged. This is confirmed directly in the unit test, where after `authorize_nonce_account` the expected `Data` still carries the same `DurableNonce::from_blockhash(...)` that was set by `initialize_nonce_account`, with only the `authority` field changed: [2](#0-1) 

The per-transaction nonce check in the SVM, `validate_transaction_nonce`, gates execution on two independent conditions: (1) the stored `durable_nonce` must match the transaction's `recent_blockhash` field, and (2) the current on-chain `authority` must be among the transaction's signers: [3](#0-2) 

Because condition (2) depends on a value (`authority`) that can be freely changed and changed back without ever touching condition (1)'s value (`durable_nonce`), a signed transaction that is rejected purely due to condition (2) failing remains fully "primed" (condition (1) still holds) and will succeed the moment the authority reverts to a signer of that transaction. The existing SVM integration test suite explicitly demonstrates the "authority changed → old-authority transaction discarded" half of this behavior: [4](#0-3) 
and, symmetrically, demonstrates that a nonce transaction succeeds as long as its signer matches the authority *currently* stored on-chain, with the durable nonce itself unaffected by the authority instruction: [5](#0-4) 

No mechanism advances or invalidates `durable_nonce` as a side effect of an authority change, so the guard against replay in this scenario relies entirely on the authority never reverting to a previous value while a stale signed transaction is outstanding — the same broken invariant identified in the Dharma report.

### Impact Explanation
Any system that manages a durable-nonce account and rotates its `authority` (e.g., custodial hot-wallet key rotation, multi-key escrow/relayer designs, batch-payment systems that alternate signing authorities) can unintentionally resurrect a transaction that was believed void. If a payout/transfer transaction was pre-signed and broadcast (or held by a counterparty) while `authority = A`, then rejected as `BlockhashNotFound`/discarded when the authority moved to `B`, and later the authority is moved back to `A` (a legitimate rollback of a botched rotation, or scheduled alternation between two authorized keys) without an intervening `AdvanceNonceAccount`, the old transaction becomes executable again by anyone who still holds it. This can cause unintended fund movement (double payment, replay of a transaction the fee payer/nonce authority believed cancelled) — a genuine fund-loss impact scoped to the system program's nonce handling in transaction/CPI processing.

### Likelihood Explanation
This does not require a malicious validator, leaked key, or trusted-plugin assumption — it only requires an application to (a) use durable nonces, (b) rotate the nonce authority more than once without an intervening advance, and (c) have a stale signed transaction still in circulation. Authority rotation-and-rollback is a realistic operational pattern (key rotation abort/retry, dual-authority scheduling), making the precondition plausible in real-world custodial/relayer deployments, though it depends on the operator's own key-management workflow rather than being exploitable by an unrelated network attacker alone.

### Recommendation
Bind nonce authority changes to the durable-nonce liveness the same way advances are bound to it: either (a) force `AuthorizeNonceAccount` to also advance `durable_nonce` (deriving a fresh nonce from the current blockhash) so that any signature captured under the old authority is permanently invalidated, or (b) document and strongly warn integrators that `AuthorizeNonceAccount` does not invalidate previously signed messages, and require them to always pair authority changes with an explicit `AdvanceNonceAccount` before or after any authority rotation. Add regression tests asserting that a transaction signed under a since-reverted authority cannot execute if resubmitted after the authority is restored.

### Proof of Concept
1. Create/initialize a nonce account with `authority = A`, `durable_nonce = H` (`programs/system/src/system_instruction.rs` `initialize_nonce_account`).
2. Sign transaction `T` that uses the nonce account with `recent_blockhash = H` and is signed by `A` (per `NONCED_TX_MARKER_IX_INDEX` signer requirement in `validate_transaction_nonce`), but do not submit it yet.
3. Submit `AuthorizeNonceAccount` changing `authority` from `A` to `B` — `durable_nonce` remains `H` (per the `authorize_inx_ok` test at `programs/system/src/system_instruction.rs:1010-1038`).
4. Submit `T`; it is discarded because `nonce_authority_is_valid` fails (`svm/src/transaction_processor.rs:873-891`), mirroring `svm/tests/integration_test.rs:1941-1979`.
5. Later, submit another `AuthorizeNonceAccount` changing `authority` back from `B` to `A` (still without any `AdvanceNonceAccount`, so `durable_nonce` is still `H`).
6. Resubmit `T`: `verify_nonce_account` succeeds (`durable_nonce` still `H`), `nonce_authority_is_valid` now succeeds (`A` is again the authority and a signer of `T`), so `T` executes — even though it was previously rejected and believed void.

### Citations

**File:** programs/system/src/system_instruction.rs (L39-58)
```rust
    let state: Versions = account.get_state()?;
    match state.state() {
        State::Initialized(data) => {
            if !signers.contains(&data.authority) {
                ic_msg!(
                    invoke_context,
                    "Advance nonce account: Account {} must be a signer",
                    data.authority
                );
                return Err(InstructionError::MissingRequiredSignature);
            }
            let next_durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            if data.durable_nonce == next_durable_nonce {
                ic_msg!(
                    invoke_context,
                    "Advance nonce account: nonce can only advance once per slot"
                );
                return Err(SystemError::NonceBlockhashNotExpired.into());
            }
```

**File:** programs/system/src/system_instruction.rs (L1010-1038)
```rust
    #[test]
    fn authorize_inx_ok() {
        prepare_mockup!(
            invoke_context,
            instruction_accounts,
            rent,
            transaction_context
        );
        push_instruction_context!(invoke_context, instruction_context, instruction_accounts);
        let mut nonce_account = instruction_context
            .try_borrow_instruction_account(NONCE_ACCOUNT_INDEX)
            .unwrap();
        let mut signers = HashSet::new();
        signers.insert(*nonce_account.get_key());
        set_invoke_context_blockhash!(invoke_context, 31);
        let authorized = *nonce_account.get_key();
        initialize_nonce_account(&mut nonce_account, &authorized, &rent, &invoke_context).unwrap();
        let authority = Pubkey::default();
        let data = nonce::state::Data::new(
            authority,
            DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash),
            invoke_context
                .environment_config
                .blockhash_lamports_per_signature,
        );
        authorize_nonce_account(&mut nonce_account, &authority, &signers, &invoke_context).unwrap();
        let versions = nonce_account.get_state::<Versions>().unwrap();
        assert_eq!(versions.state(), &State::Initialized(data));
    }
```

**File:** svm/src/transaction_processor.rs (L861-891)
```rust
        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };

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
```

**File:** svm/tests/integration_test.rs (L1941-1979)
```rust
    // batch 10:
    // * a successful blockhash transaction that changes the nonce authority
    // * a nonce transaction that uses the nonce with the old authority; this transaction must be dropped
    if !fee_paying_nonce {
        let mut test_entry = common_test_entry.clone();

        let new_authority = Pubkey::new_unique();

        let first_transaction = Transaction::new_signed_with_payer(
            &[system_instruction::authorize_nonce_account(
                &nonce_pubkey,
                &fee_payer,
                &new_authority,
            )],
            Some(&fee_payer),
            &[&fee_payer_keypair],
            Hash::default(),
        );

        test_entry.push_transaction(first_transaction);
        test_entry.push_nonce_transaction_with_status(
            second_transaction,
            nonce_pubkey,
            ExecutionStatus::Discarded,
        );

        let final_nonce_data =
            nonce::state::Data::new(new_authority, initial_durable, LAMPORTS_PER_SIGNATURE);
        let final_nonce_account = AccountSharedData::new_data(
            LAMPORTS_PER_SOL,
            &nonce::versions::Versions::new(nonce::state::State::Initialized(final_nonce_data)),
            &system_program::id(),
        )
        .unwrap();

        test_entry.update_expected_account_data(nonce_pubkey, &final_nonce_account);

        test_entries.push(test_entry);
    }
```

**File:** svm/tests/integration_test.rs (L1981-2028)
```rust
    // batch 11:
    // * a successful blockhash transaction that changes the nonce authority
    // * a nonce transaction that uses the nonce with the new authority; this transaction succeeds
    if !fee_paying_nonce {
        let mut test_entry = common_test_entry;

        let new_authority_keypair = Keypair::new();
        let new_authority = new_authority_keypair.pubkey();

        let first_transaction = Transaction::new_signed_with_payer(
            &[system_instruction::authorize_nonce_account(
                &nonce_pubkey,
                &fee_payer,
                &new_authority,
            )],
            Some(&fee_payer),
            &[&fee_payer_keypair],
            Hash::default(),
        );

        let second_transaction = Transaction::new_signed_with_payer(
            &[
                system_instruction::advance_nonce_account(&nonce_pubkey, &new_authority),
                successful_noop_instruction,
            ],
            Some(&fee_payer),
            &[&fee_payer_keypair, &new_authority_keypair],
            *initial_durable.as_hash(),
        );

        test_entry.push_transaction(first_transaction);
        test_entry.push_nonce_transaction(second_transaction, nonce_pubkey);

        test_entry.decrease_expected_lamports(&fee_payer, LAMPORTS_PER_SIGNATURE * 2);

        let final_nonce_data =
            nonce::state::Data::new(new_authority, advanced_durable, LAMPORTS_PER_SIGNATURE);
        let final_nonce_account = AccountSharedData::new_data(
            LAMPORTS_PER_SOL,
            &nonce::versions::Versions::new(nonce::state::State::Initialized(final_nonce_data)),
            &system_program::id(),
        )
        .unwrap();

        test_entry.update_expected_account_data(nonce_pubkey, &final_nonce_account);

        test_entries.push(test_entry);
    }
```
