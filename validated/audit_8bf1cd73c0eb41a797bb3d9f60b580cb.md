### Title
Gateway Accepts Invoke Transaction with Invalid Signature via Validation-Skip for Pending Deploy-Account Accounts - (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The `skip_stateful_validations` function in the Apollo gateway unconditionally skips the `__validate__` entry-point (i.e., signature verification) for any invoke transaction whose nonce equals 1 and whose sender address appears in the mempool or a recent block. An unprivileged attacker who observes a victim's `deploy_account` transaction in the public mempool can immediately submit a crafted invoke transaction with a forged signature on behalf of the victim. The gateway admits it without running `__validate__`. When the batcher later executes the block, `__validate__` is called and fails, the transaction reverts, the nonce is incremented, and the fee is charged from the victim's balance — a direct economic griefing with no profit to the attacker.

---

### Finding Description

`skip_stateful_validations` is called inside `run_pre_validation_checks` to decide whether to call `run_validate_entry_point`: [1](#0-0) 

The skip condition is:

```
tx is Invoke
AND tx.nonce() == Nonce(Felt::ONE)
AND account_nonce == Nonce(Felt::ZERO)
AND mempool_client.account_tx_in_pool_or_recent_block(sender) == true
```

When all four conditions hold, `skip_validate = true` is returned, and `run_validate_entry_point` is never called: [2](#0-1) 

The `account_tx_in_pool_or_recent_block` check is satisfied as soon as the victim's `deploy_account` transaction appears in the public mempool: [3](#0-2) 

The gateway therefore admits the attacker's invoke — which carries an arbitrary/forged signature — into the mempool without any cryptographic verification.

The execution flags (`validate = false`) are **not** persisted with the `InternalRpcTransaction` stored in the mempool. When the batcher later executes the block, it constructs its own `AccountTransaction` with `validate = true` and calls `__validate__`. The forged signature fails, the transaction reverts, the nonce is incremented (preventing replay), and the fee is deducted from the victim's balance.

The `max_nonce_for_validation_skip` configuration parameter (default `0x1`) bounds the window to nonce = 1 per deployment, but the attack is fully reachable with the default configuration: [4](#0-3) 

---

### Impact Explanation

**Impact: High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

The gateway admits a transaction whose signature has never been verified. The victim's account is charged the full transaction fee for a reverted transaction they never authorized. The attacker spends nothing (no L1 cost, no fee token balance required). The attack is repeatable across every new account deployment observed in the mempool.

If `max_nonce_for_validation_skip` is raised above `0x1` (e.g., to improve UX for multi-step account setups), the attacker can submit multiple fee-draining transactions per deployment window.

---

### Likelihood Explanation

The mempool is public. Any observer can detect a `deploy_account` transaction the moment it is gossiped. The attacker needs only to:

1. Watch the mempool for `deploy_account` transactions.
2. Craft an invoke with `sender_address = victim`, `nonce = 1`, arbitrary calldata, and a forged/zero signature.
3. Submit it to the gateway before the `deploy_account` is committed.

No privileged access, no leaked keys, no special infrastructure is required.

---

### Recommendation

Replace the mempool-presence heuristic with a cryptographically sound check. Two options:

1. **Require the caller to supply the `deploy_account` transaction hash** (as the `PyValidator` path already does in `crates/native_blockifier/src/py_validator.rs` lines 69–89) and verify that the hash matches a pending `deploy_account` in the mempool before skipping `__validate__`.

2. **Never skip `__validate__` at the gateway.** Instead, accept the UX cost of requiring the user to submit the `deploy_account` first and wait for it to be committed before submitting the invoke. This is the safest option. [5](#0-4) 

---

### Proof of Concept

**Preconditions:**
- Victim generates a new keypair and computes their future account address `A`.
- Victim submits a `deploy_account` transaction (nonce = 0) to the gateway. It enters the mempool.

**Attack steps:**

```
1. Attacker monitors the mempool and observes deploy_account for address A.

2. Attacker constructs an InvokeTransactionV3:
     sender_address = A
     nonce          = 1
     calldata       = [arbitrary, e.g. transfer all STRK to attacker]
     signature      = [0x0, 0x0]   ← forged / invalid

3. Attacker submits the invoke to the gateway RPC endpoint
   (starknet_addInvokeTransaction).

4. Gateway evaluation in skip_stateful_validations:
     tx.nonce() == Nonce(1)          ✓
     account_nonce == Nonce(0)       ✓  (A not yet deployed)
     account_tx_in_pool_or_recent_block(A) == true  ✓  (deploy_account is in pool)
   → skip_validate = true
   → run_validate_entry_point is NOT called
   → transaction admitted to mempool

5. Batcher builds the next block:
     - Executes deploy_account (nonce 0→1, account A created with balance B)
     - Executes attacker's invoke (nonce 1):
         __validate__ called → signature [0,0] fails
         transaction reverts
         nonce incremented to 2
         fee F charged from A's balance

6. Victim's account A now has balance B − F and nonce 2.
   Victim's own legitimate invoke (nonce 1) is now rejected
   (nonce 1 < account nonce 2) and must be resubmitted.
```

**Observed wrong value:** The gateway's admission decision treats a transaction with an unverified (forged) signature as valid, producing an incorrect `true` result for the admission check — a wrong authoritative value fed into the sequencing pipeline.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L275-286)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
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

**File:** crates/apollo_mempool/src/mempool.rs (L658-661)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L240-251)
```rust
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

**File:** crates/native_blockifier/src/py_validator.rs (L98-121)
```rust
    pub fn should_run_stateful_validations(
        &mut self,
        account_tx: &AccountTransaction,
        deploy_account_tx_hash: Option<TransactionHash>,
    ) -> StatefulValidatorResult<bool> {
        if account_tx.tx_type() != TransactionType::InvokeFunction {
            return Ok(true);
        }
        let tx_info = account_tx.create_tx_info();
        let nonce = self.stateful_validator.get_nonce(tx_info.sender_address())?;

        let deploy_account_not_processed =
            deploy_account_tx_hash.is_some() && nonce == Nonce(Felt::ZERO);
        let tx_nonce = tx_info.nonce();
        let is_post_deploy_nonce = Nonce(Felt::ONE) <= tx_nonce;
        let nonce_small_enough_to_qualify_for_validation_skip =
            tx_nonce <= self.max_nonce_for_validation_skip;

        let skip_validate = deploy_account_not_processed
            && is_post_deploy_nonce
            && nonce_small_enough_to_qualify_for_validation_skip;

        Ok(!skip_validate)
    }
```
