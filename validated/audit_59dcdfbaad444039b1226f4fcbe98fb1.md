### Title
Multisig `MultisigTransactionPayload::Script` execution bypasses `MULTISIG_SCRIPT` feature gate when replayed from on-chain storage - (File: `types/src/transaction/multisig.rs`, `aptos-move/aptos-vm/src/aptos_vm.rs`)

### Summary
The `FEATURE_UNDER_GATING` check for multisig script payloads is only evaluated against the `TransactionExecutableRef` of the *currently submitted* transaction. It is never re-checked against the `MultisigTransactionPayload` that is fetched from on-chain storage and actually executed. If a Script payload is stored on-chain while `MULTISIG_SCRIPT` is enabled (or is stored via any other bypass of the Rust-level check), it can still be executed via `validate_and_execute_script` after the feature is disabled, as long as the executing transaction supplies an empty/no local payload (`TransactionExecutableRef::Empty`).

### Finding Description
`Multisig::as_transaction_executable_ref` maps a submitted transaction's local `transaction_payload` to `TransactionExecutableRef::Script`, `EntryFunction`, or `Empty` [1](#0-0) . When the payload is not attached to the current transaction (i.e., it was previously stored on-chain via `create_transaction`), this resolves to `TransactionExecutableRef::Empty`.

All three feature-gate checks for `MULTISIG_SCRIPT` are keyed off this `executable` argument matching the `Script` arm, and none of them ever runs when `executable` is `Empty`:

- `run_prologue_with_payload`: the gate only fires when `executable.is_script()` [2](#0-1) .
- `run_multisig_prologue`: the gate only fires inside the `TransactionExecutableRef::Script(script)` match arm [3](#0-2) .
- `execute_multisig_transaction`: the gate only fires inside the `TransactionExecutableRef::Script(script)` match arm; the `Empty` arm builds a plain `vec![]`/`bcs::to_bytes(Vec::<u8>::new())` payload with no feature check [4](#0-3) .

After the `Empty` arm passes, the actual stored payload is retrieved by calling `GET_NEXT_TRANSACTION_PAYLOAD` and deserialized into a `MultisigTransactionPayload` enum, which can be `Script` [5](#0-4) . This deserialized payload is passed straight into `execute_multisig_payload`, whose `Script` arm calls `self.validate_and_execute_script(...)` under the multisig account's signer with **no feature-flag check at all**: [6](#0-5) .

The Move-side `MultisigTransactionPayload` enum type (`EntryFunction` / `Script`) is defined purely in Rust [7](#0-6) ; on the Move module side, transaction creation stores this payload as opaque `vector<u8>` and only validates quorum/timelock/hash matching (`validate_multisig_transaction`), not the payload's variant type or any feature flag [8](#0-7) . I was unable to fully inspect the Move `create_transaction` entry function body in this pass (grep did not resolve it precisely), so I cannot confirm with certainty whether creation-time itself enforces the feature flag; this is a residual unknown.

### Impact Explanation
This lets a governance-level "kill switch" (disabling `FeatureFlag::MULTISIG_SCRIPT`) fail to stop already-created multisig script transactions from executing. Since script execution under a multisig account's signer can call arbitrary bytecode (subject to normal script verification, but not to the entry-function visibility/module boundaries), any pre-existing queued Script payload continues to run as the multisig account after the flag is turned off, defeating the intended protection semantics of the feature gate. This affects a "protected state mutation" path (multisig-authorized arbitrary script execution), matching the reviewed scope of native-validation-gating bypass.

### Likelihood Explanation
Requires: (a) a multisig transaction with a `Script` payload to exist in on-chain storage (created while the feature was enabled, or via any other creation-time gap), and (b) the executing owner to submit the execution transaction without re-attaching the payload (the normal/default execute flow, since the payload is "already stored on chain"). This is a standard, unprivileged multisig execution pattern — no special privilege beyond being a normal multisig owner is required.

### Recommendation
Re-validate `is_multisig_script_enabled()` against the *deserialized on-chain* `MultisigTransactionPayload` variant inside `execute_multisig_payload` (and/or immediately after deserializing `payload` in `execute_multisig_transaction`, before calling `execute_multisig_payload`), rather than relying solely on the transaction's local `TransactionExecutableRef`.

### Proof of Concept
1. Enable `FeatureFlag::MULTISIG_SCRIPT`, create a multisig transaction with a `Script` payload via `create_multisig_transaction` (stores the payload/hash on-chain) — matches test setup in `test_multisig_script_transaction_with_matching_payload` [9](#0-8) .
2. Disable `FeatureFlag::MULTISIG_SCRIPT`.
3. Call `execute_multisig_transaction` (no `transaction_payload` attached, i.e. `Multisig { transaction_payload: None }`), exactly as in `TestContext::execute_multisig_transaction` [10](#0-9) .
4. Observe that execution proceeds successfully (or fails only on payload/hash mismatch, not `FEATURE_UNDER_GATING`), because `TransactionExecutableRef::Empty` skips every feature check, and `execute_multisig_payload`'s `Script` arm has none.

### Citations

**File:** types/src/transaction/multisig.rs (L19-24)
```rust
/// Enum for multisig transaction payloads, supporting both entry functions and scripts.
#[derive(Clone, Debug, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub enum MultisigTransactionPayload {
    EntryFunction(EntryFunction),
    Script(Script),
}
```

**File:** types/src/transaction/multisig.rs (L53-63)
```rust
    pub fn as_transaction_executable_ref(&self) -> TransactionExecutableRef<'_> {
        match &self.transaction_payload {
            Some(MultisigTransactionPayload::EntryFunction(entry)) => {
                TransactionExecutableRef::EntryFunction(entry)
            },
            Some(MultisigTransactionPayload::Script(script)) => {
                TransactionExecutableRef::Script(script)
            },
            None => TransactionExecutableRef::Empty,
        }
    }
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1305-1335)
```rust
        let provided_payload = match executable {
            TransactionExecutableRef::EntryFunction(entry_func) => {
                // TODO[Orderless]: For backward compatibility reasons, still using `MultisigTransactionPayload` here.
                // Find a way to deprecate this.
                bcs::to_bytes(&MultisigTransactionPayload::EntryFunction(
                    entry_func.clone(),
                ))
                .map_err(|_| invariant_violation_error())?
            },
            TransactionExecutableRef::Empty => {
                // Default to empty bytes if payload is not provided.
                if self
                    .features()
                    .is_abort_if_multisig_payload_mismatch_enabled()
                {
                    vec![]
                } else {
                    bcs::to_bytes::<Vec<u8>>(&vec![]).map_err(|_| invariant_violation_error())?
                }
            },
            TransactionExecutableRef::Script(script) => {
                if !self.features().is_multisig_script_enabled() {
                    let s = VMStatus::error(
                        StatusCode::FEATURE_UNDER_GATING,
                        Some("Multisig script payload is not enabled".to_string()),
                    );
                    return Ok((s, discarded_output(StatusCode::FEATURE_UNDER_GATING)));
                }
                bcs::to_bytes(&MultisigTransactionPayload::Script(script.clone()))
                    .map_err(|_| invariant_violation_error())?
            },
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1386-1389)
```rust
        let payload_bytes =
            bcs::from_bytes::<Vec<u8>>(payload_bytes).map_err(|_| deserialization_error())?;
        let payload = bcs::from_bytes::<MultisigTransactionPayload>(&payload_bytes)
            .map_err(|_| deserialization_error())?;
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1468-1505)
```rust
    fn execute_multisig_payload(
        &self,
        resolver: &impl AptosMoveResolver,
        code_storage: &impl AptosCodeStorage,
        mut session: UserSession,
        gas_meter: &mut impl AptosGasMeter,
        traversal_context: &mut TraversalContext,
        multisig_address: AccountAddress,
        payload: &MultisigTransactionPayload,
        change_set_configs: &ChangeSetConfigs,
        trace_recorder: &mut impl TraceRecorder,
    ) -> Result<UserSessionChangeSet, VMStatus> {
        let serialized_signers =
            SerializedSigners::new(vec![serialized_signer(&multisig_address)], None);

        // If txn args are not valid, we'd still consider the transaction as executed but
        // failed. This is primarily because it's unrecoverable at this point.
        session.execute(|session| match payload {
            MultisigTransactionPayload::EntryFunction(entry_function) => self
                .validate_and_execute_entry_function(
                    code_storage,
                    session,
                    &serialized_signers,
                    gas_meter,
                    traversal_context,
                    entry_function,
                    trace_recorder,
                ),
            MultisigTransactionPayload::Script(script) => self.validate_and_execute_script(
                session,
                &serialized_signers,
                code_storage,
                gas_meter,
                traversal_context,
                script,
                trace_recorder,
            ),
        })?;
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L3120-3128)
```rust
        if executable.is_script()
            && extra_config.is_multisig()
            && !self.features().is_multisig_script_enabled()
        {
            return Err(VMStatus::error(
                StatusCode::FEATURE_UNDER_GATING,
                Some("Script payload not yet supported for multisig transactions".to_string()),
            ));
        }
```

**File:** aptos-move/aptos-vm/src/transaction_validation.rs (L444-453)
```rust
        TransactionExecutableRef::Script(script) => {
            if !features.is_multisig_script_enabled() {
                return Err(VMStatus::error(
                    StatusCode::FEATURE_UNDER_GATING,
                    Some("Multisig script payload is not enabled".to_string()),
                ));
            }
            bcs::to_bytes(&MultisigTransactionPayload::Script(script.clone()))
                .map_err(|_| unreachable_error.clone())?
        },
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L1328-1371)
```text
    fun validate_multisig_transaction(
        owner: &signer, multisig_account: address, payload: vector<u8>) {
        assert_multisig_account_exists(multisig_account);
        assert_is_owner(owner, multisig_account);
        let sequence_number = last_resolved_sequence_number(multisig_account) + 1;
        assert_transaction_exists(multisig_account, sequence_number);

        if (features::multisig_v2_enhancement_feature_enabled()) {
            assert!(
                can_execute(address_of(owner), multisig_account, sequence_number),
                error::invalid_argument(ENOT_ENOUGH_APPROVALS),
            );
        }
        else {
            assert!(
                can_be_executed(multisig_account, sequence_number),
                error::invalid_argument(ENOT_ENOUGH_APPROVALS),
            );
        };

        // Count approvals, including the executing owner's implicit vote.
        let (num_approvals, _) = num_approvals_and_rejections(multisig_account, sequence_number);
        if (!has_voted_for_approval(multisig_account, sequence_number, address_of(owner))) {
            num_approvals += 1;
        };
        assert!(num_approvals >= num_signatures_required(multisig_account), error::invalid_argument(ENOT_ENOUGH_APPROVALS));

        // Timelock check — separate from quorum so the error is unambiguous.
        assert!(
            can_execute_with_timelock(multisig_account, sequence_number, num_approvals),
            error::invalid_state(ETIMELOCK_NOT_EXPIRED),
        );

        // If the transaction payload is not stored on chain, verify that the provided payload matches the hashes stored
        // on chain.
        let multisig_account_resource = borrow_global<MultisigAccount>(multisig_account);
        let transaction = multisig_account_resource.transactions.borrow(sequence_number);
        if (transaction.payload_hash.is_some()) {
            let payload_hash = transaction.payload_hash.borrow();
            assert!(
                sha3_256(payload) == *payload_hash,
                error::invalid_argument(EPAYLOAD_DOES_NOT_MATCH_HASH),
            );
        };
```

**File:** api/src/tests/multisig_transactions_test.rs (L985-996)
```rust
    context
        .enable_feature(FeatureFlag::MULTISIG_SCRIPT as u64)
        .await;
    let owner_account = &mut context.create_account().await;
    let multisig_account = context
        .create_multisig_account(owner_account, vec![], 1, 1000)
        .await;
    assert_eq!(1000, context.get_apt_balance(multisig_account).await);
    let multisig_payload = construct_multisig_txn_script_payload(owner_account.address(), 1000);
    context
        .create_multisig_transaction(owner_account, multisig_account, multisig_payload.clone())
        .await;
```

**File:** api/test-context/src/test_context.rs (L558-573)
```rust
    pub async fn execute_multisig_transaction(
        &mut self,
        owner: &mut LocalAccount,
        multisig_account: AccountAddress,
        expected_status_code: u16,
    ) {
        self.api_execute_txn_expecting(
            owner,
            json!({
                "type": "multisig_payload",
                "multisig_address": multisig_account.to_hex_literal(),
            }),
            expected_status_code,
        )
        .await;
    }
```
