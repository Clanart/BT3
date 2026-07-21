### Title
Signature Verification Bypass via Unconditional `__validate__` Skip for Pending-Deploy Accounts — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The `skip_stateful_validations` function in the gateway's stateful transaction validator unconditionally skips the `__validate__` entry point — the sole on-chain signature-verification step — for any invoke transaction with `nonce=1` when the sender account has **any** pending transaction in the mempool. An attacker who observes a victim's `deploy_account` transaction in the mempool can submit a fake invoke transaction (arbitrary signature, victim's `sender_address`, `nonce=1`) that passes all gateway checks and is admitted to the mempool without signature verification.

---

### Finding Description

`skip_stateful_validations` is a UX feature that lets users broadcast `deploy_account + invoke` atomically before the account is deployed on-chain. Its logic is:

```
if tx.nonce() == 1 && account_nonce == 0 {
    return mempool_client.account_tx_in_pool_or_recent_block(sender_address)
}
``` [1](#0-0) 

When this returns `true`, `run_validate_entry_point` sets `validate: !skip_validate` → `validate: false` in `ExecutionFlags`: [2](#0-1) 

`StatefulValidator::perform_validations` then returns early before calling `__validate__`: [3](#0-2) 

The authorization invariant that is broken: **the function never verifies that the invoke transaction's signature belongs to the same key-pair as the deploy_account transaction**. The mempool check `account_tx_in_pool_or_recent_block` returns `true` for any transaction from that address — it is not restricted to `deploy_account` type: [4](#0-3) 

**Attack path:**

1. Victim broadcasts a `deploy_account` tx (nonce 0) → it enters the mempool.
2. Attacker submits an invoke tx: `sender_address = victim`, `nonce = 1`, `signature = [0x0, 0x0]` (or any bytes).
3. Gateway: `account_nonce == 0`, `tx_nonce == 1`, `account_tx_in_pool_or_recent_block(victim) == true` → `skip_validate = true` → `__validate__` is **not called**.
4. The fee/nonce pre-checks in `perform_pre_validation_stage` still run (balance check against victim's funded-but-undeployed account), but no signature check occurs.
5. The attacker's invalid transaction is forwarded to the mempool and accepted.

When the batcher later picks up the transaction it uses `AccountTransaction::new_for_sequencing`, which hard-codes `validate: true`: [5](#0-4) 

So `__validate__` is called at execution time and the transaction fails. However, the invalid transaction was already **admitted to the mempool without authorization**, which is the root impact.

---

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

- Any attacker can inject signature-less invoke transactions for any victim who has a pending `deploy_account` in the mempool.
- The invalid transactions consume mempool slots and block capacity.
- Because `__validate__` fails at execution time, no fee is charged to the attacker (Starknet does not charge fees for `__validate__` failures), enabling free-of-cost mempool spam targeting new accounts.
- The victim's legitimate nonce-1 invoke transaction may be displaced or delayed by the attacker's fake transaction occupying the same (address, nonce) slot, depending on mempool fee-escalation rules.

---

### Likelihood Explanation

**Medium.** The preconditions are:

1. The victim must have a `deploy_account` transaction in the mempool (common for all new Starknet accounts).
2. The victim's account must have sufficient pre-funded balance to pass the gateway's `verify_can_pay_committed_bounds` check (standard practice: fund before deploy).

Both conditions are routinely satisfied during normal account onboarding. An attacker can passively monitor the mempool for `deploy_account` transactions and immediately submit fake invoke transactions.

---

### Recommendation

1. **Restrict the skip to deploy-account-only presence**: change `account_tx_in_pool_or_recent_block` to a dedicated `has_pending_deploy_account(address)` query that only returns `true` when a `DeployAccount` transaction (not any transaction) is pending for that address.
2. **Alternatively, remove the UX skip entirely** and require users to wait for `deploy_account` confirmation before submitting invoke transactions. The UX cost is minor compared to the security risk.
3. If the skip must be retained, **bind the invoke transaction hash to the deploy_account transaction hash** (e.g., require the invoke to reference the deploy_account tx hash in its calldata or a dedicated field) so the gateway can verify they originate from the same submitter.

---

### Proof of Concept

```
// Precondition: victim has funded their account at address VICTIM_ADDR.

// Step 1: Victim submits deploy_account (nonce 0) — passes __validate_deploy__.
gateway.add_tx(deploy_account_tx { sender: VICTIM_ADDR, nonce: 0, sig: victim_sig })
// → mempool now contains VICTIM_ADDR

// Step 2: Attacker submits fake invoke (nonce 1, bogus signature).
gateway.add_tx(invoke_tx {
    sender_address: VICTIM_ADDR,
    nonce: 1,
    calldata: [attacker_contract, ...],
    signature: [0x0],   // ← invalid, never verified at gateway
})
// Gateway path:
//   account_nonce = 0, tx_nonce = 1
//   skip_stateful_validations → account_tx_in_pool_or_recent_block(VICTIM_ADDR) = true
//   → validate = false → __validate__ NOT called
//   → transaction ACCEPTED into mempool

// Step 3: Batcher executes the fake invoke with validate=true.
//   __validate__ runs → fails (bad signature) → tx reverted, no fee charged.
//   Victim's legitimate nonce-1 invoke is displaced or delayed.
```

The root cause is in `skip_stateful_validations` at: [1](#0-0) 

which sets the `validate: false` flag via: [6](#0-5) 

causing `StatefulValidator::perform_validations` to return before the `__validate__` call: [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L148-150)
```rust
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L283-285)
```rust
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L401-432)
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
```

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L76-81)
```rust
            ApiTransaction::Invoke(_) => {
                let tx_context = Arc::new(self.tx_executor.block_context.to_tx_context(&tx));
                tx.perform_pre_validation_stage(self.state(), &tx_context)?;
                if !tx.execution_flags.validate {
                    return Ok(());
                }
```

**File:** crates/apollo_mempool/src/mempool.rs (L658-661)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
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
