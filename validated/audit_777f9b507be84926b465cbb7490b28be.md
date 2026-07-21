### Title
Dual Validation-Skip Mechanisms Allow Forged-Signature Invoke Transactions to Bypass Gateway Admission — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's `skip_stateful_validations` function hardcodes the nonce threshold for skipping `__validate__` entry-point execution to `Nonce(Felt::ONE)`, while `StatefulTransactionValidatorConfig` exposes a separate, documented `max_nonce_for_validation_skip` field that the gateway silently ignores. The `PyValidator` in `native_blockifier` correctly consults the config field; the gateway does not. This dual-mechanism inconsistency means (a) an operator who sets `max_nonce_for_validation_skip: 0` to disable the skip has no effect on the gateway path, and (b) any attacker who observes an address with on-chain nonce 0 and any pending transaction in the mempool can submit an Invoke with nonce=1 and a forged signature that passes all gateway checks and is admitted to the mempool without signature verification.

---

### Finding Description

**Mechanism 1 — hardcoded gateway skip (`skip_stateful_validations`)** [1](#0-0) 

The function checks `tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO)` and, if the address appears in the mempool via `account_tx_in_pool_or_recent_block`, returns `true` (skip). It takes no config argument and never reads `max_nonce_for_validation_skip`.

**Mechanism 2 — configurable field that the gateway ignores** [2](#0-1) 

`StatefulTransactionValidatorConfig.max_nonce_for_validation_skip` is documented as "Maximum nonce for which the validation is skipped" and defaults to `Nonce(Felt::ONE)`. It is serialised into the production config schema: [3](#0-2) 

**Where the config field IS used — PyValidator (different code path)** [4](#0-3) 

`PyValidator::should_run_stateful_validations` reads `self.max_nonce_for_validation_skip` and enforces the configurable threshold. The gateway's `skip_stateful_validations` never does.

**How the skip propagates to suppress `__validate__`** [5](#0-4) [6](#0-5) 

`validate: !skip_validate` — when `skip_validate` is `true`, the `ExecutionFlags.validate` field is set to `false`, and `StatefulValidator::perform_validations` returns immediately without calling `__validate__`: [7](#0-6) 

**Mempool check does not verify signatures**

`validate_by_mempool` calls `mempool_client.validate_tx`, which only checks nonce ordering and duplicates — it does not re-run the account's `__validate__` entry point: [8](#0-7) [9](#0-8) 

---

### Impact Explanation

**Impact: High — Mempool/gateway admission accepts invalid transactions before sequencing.**

An attacker who observes address `A` with on-chain nonce 0 and any entry in the mempool (e.g., a pending deploy_account submitted by the legitimate owner) can submit `Invoke(sender=A, nonce=1, signature=<forged>)`. The gateway:

1. Reads on-chain nonce 0 → satisfies `account_nonce == Nonce(Felt::ZERO)`.
2. Sees `tx.nonce() == 1` → satisfies the hardcoded condition.
3. Calls `account_tx_in_pool_or_recent_block(A)` → returns `true`.
4. Sets `skip_validate = true` → `ExecutionFlags { validate: false }`.
5. Calls `StatefulValidator::perform_validations` which returns `Ok(())` without running `__validate__`.
6. Admits the forged-signature transaction to the mempool.

The batcher later executes the transaction with `validate: true` (via `AccountTransaction::new_for_sequencing`): [10](#0-9) 

The `__validate__` entry point then fails, the transaction is reverted, and it is included in the block as a reverted transaction — consuming block capacity and charging fees from the account balance. The attacker does not need to control address `A`; they only need to observe a pending deploy_account for it.

Additionally, an operator who sets `max_nonce_for_validation_skip: 0` in the config to disable the skip entirely has no effect on the gateway path. The gateway continues to skip validation for nonce-1 transactions regardless of the config value, silently violating the operator's security intent.

---

### Likelihood Explanation

**Medium.** The precondition — an address with on-chain nonce 0 and a pending transaction in the mempool — is a normal, observable state during the deploy_account + invoke UX flow. The mempool is observable by any network participant. No privileged access is required. The attacker only needs to craft an Invoke transaction with the target address as sender and nonce=1; the signature field can be arbitrary.

---

### Recommendation

1. **Unify the skip logic**: Pass `config.max_nonce_for_validation_skip` into `skip_stateful_validations` and replace the hardcoded `Nonce(Felt::ONE)` with the config value. This makes the gateway and `PyValidator` consistent and gives operators a single, effective control point.

2. **Restrict the skip to deploy_account context**: `account_tx_in_pool_or_recent_block` returns `true` for any transaction type, not only deploy_account. The check should be narrowed to verify that the pending transaction for the address is specifically a `DeployAccount`, preventing the skip from triggering for addresses that merely have pending Invoke transactions.

3. **Add a cross-validation assertion**: If `max_nonce_for_validation_skip == Nonce(Felt::ZERO)`, assert that `skip_validate` is never set to `true`, and emit a startup warning if the config value diverges from the hardcoded gateway behaviour.

---

### Proof of Concept

```
1. Legitimate user submits DeployAccount(sender=A, nonce=0, sig=valid_sig_A).
   → Mempool admits it; account_tx_in_pool_or_recent_block(A) now returns true.
   → On-chain nonce of A remains 0 (not yet committed).

2. Attacker submits Invoke(sender=A, nonce=1, sig=0xdeadbeef_forged).

3. Gateway stateful validator:
   a. get_nonce_from_state(A) → Nonce(0)          [on-chain nonce still 0]
   b. validate_nonce: nonce 1 is within gap(200)  [passes]
   c. validate_by_mempool: nonce ordering OK       [passes, no sig check]
   d. skip_stateful_validations:
      - tx.nonce() == Nonce(1) ✓
      - account_nonce == Nonce(0) ✓
      - account_tx_in_pool_or_recent_block(A) → true ✓
      → returns skip_validate = true
   e. run_validate_entry_point(skip_validate=true):
      - ExecutionFlags { validate: false }
      - StatefulValidator::perform_validations returns Ok(()) immediately
      → __validate__ NOT called; forged signature NOT checked

4. Forged Invoke admitted to mempool.

5. Batcher executes with validate=true → __validate__ fails → transaction reverted.
   Reverted transaction included in block; block capacity consumed.

6. Operator sets max_nonce_for_validation_skip: 0x0 in config_schema.json.
   Gateway still skips validation for nonce-1 transactions (config field unused).
   Operator's security control is silently ineffective.
```

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L148-151)
```rust
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L283-285)
```rust
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L386-396)
```rust
async fn validate_by_mempool(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<()> {
    let validation_args = ValidationArgs::new(tx, account_nonce);
    mempool_client
        .validate_tx(validation_args)
        .await
        .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L401-433)
```rust
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
    }

    Ok(false)
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L227-251)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    // If true, ensures the max L2 gas price exceeds (a configurable percentage of) the base gas
    // price of the previous block.
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}

impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
}
```

**File:** crates/apollo_node/resources/config_schema.json (L2767-2771)
```json
  "gateway_config.static_config.stateful_tx_validator_config.max_nonce_for_validation_skip": {
    "description": "Maximum nonce for which the validation is skipped.",
    "privacy": "Public",
    "value": "0x1"
  },
```

**File:** crates/native_blockifier/src/py_validator.rs (L113-118)
```rust
        let nonce_small_enough_to_qualify_for_validation_skip =
            tx_nonce <= self.max_nonce_for_validation_skip;

        let skip_validate = deploy_account_not_processed
            && is_post_deploy_nonce
            && nonce_small_enough_to_qualify_for_validation_skip;
```

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L79-81)
```rust
                if !tx.execution_flags.validate {
                    return Ok(());
                }
```

**File:** crates/apollo_mempool/src/communication.rs (L143-146)
```rust
    fn validate_tx(&mut self, args: ValidationArgs) -> MempoolResult<()> {
        self.mempool.validate_tx(args)?;
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L147-155)
```rust
    pub fn new_for_sequencing(tx: Transaction) -> Self {
        let execution_flags = ExecutionFlags {
            only_query: false,
            charge_fee: enforce_fee(&tx, false),
            validate: true,
            strict_nonce_check: true,
        };
        AccountTransaction { tx, execution_flags }
    }
```
