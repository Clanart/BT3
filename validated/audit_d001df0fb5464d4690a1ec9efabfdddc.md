Looking at the `skip_stateful_validations` function and the `account_tx_in_pool_or_recent_block` check in the gateway, I can identify a direct analog to the `_notSameBlock()` bypass. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Title
`skip_stateful_validations` Bypasses `__validate__` Signature Check via Weak `account_tx_in_pool_or_recent_block` Proxy — (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The gateway's `skip_stateful_validations` function skips the `__validate__` entry-point (signature verification) for an Invoke V3 transaction with `nonce=1` whenever `account_tx_in_pool_or_recent_block` returns `true` for the sender address. That helper returns `true` for **any** transaction in the pool or a recent block for that address — not specifically a `DeployAccount` transaction. An unprivileged attacker who observes a victim's `DeployAccount` in the public mempool can immediately submit an Invoke with `nonce=1` and an **invalid signature** for the same address; the gateway accepts it without running `__validate__`, admitting an unauthorized transaction into the mempool.

### Finding Description

`skip_stateful_validations` (lines 401–432) is a UX feature: when a user sends `DeployAccount + Invoke(nonce=1)` simultaneously, the account does not yet exist on-chain, so running `__validate__` would fail. The function therefore skips signature verification when:

1. `tx.nonce() == Nonce(Felt::ONE)`
2. `account_nonce == Nonce(Felt::ZERO)`
3. `account_tx_in_pool_or_recent_block(sender_address)` returns `true` [4](#0-3) 

The comment reads: *"It is sufficient to check if the account exists in the mempool since it means that either it has a deploy_account transaction or transactions with future nonces that passed validations."*

However, `account_tx_in_pool_or_recent_block` is:

```rust
pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
    self.state.contains_account(account_address)
        || self.tx_pool.contains_account(account_address)
}
``` [2](#0-1) 

This returns `true` for **any** transaction associated with the address — including a `DeployAccount` submitted by a completely different party. The check is therefore not a reliable proxy for "the legitimate owner submitted a `DeployAccount`."

When `skip_validate=true`, `run_validate_entry_point` sets `validate: false`:

```rust
let execution_flags =
    ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
``` [5](#0-4) 

With `validate=false`, `validate_tx` returns early without calling `__validate__`:

```rust
if !self.execution_flags.validate {
    return Ok(None);
}
``` [6](#0-5) 

The transaction is then forwarded to the mempool with no signature check performed.

### Impact Explanation

**High — Mempool/gateway admission accepts invalid transactions before sequencing.**

An attacker submits an Invoke with `nonce=1` and an arbitrary/invalid signature for a victim account whose `DeployAccount` is already in the mempool. The gateway skips `__validate__` and admits the transaction. When the batcher later executes the block:

- The `DeployAccount` succeeds, deploying the account.
- The attacker's Invoke is executed; `__validate__` is now called by the blockifier (which always uses `validate: true`). The invalid signature causes the transaction to revert.
- Per Starknet protocol, a reverted `__validate__` still **increments the nonce** and **charges a fee** (if the account has funds).
- The victim's legitimate `Invoke(nonce=1)` is now permanently rejected with `NonceTooOld`.

This constitutes unauthorized nonce consumption and potential fee drain against a newly deployed account, triggered entirely by an unprivileged third party.

### Likelihood Explanation

`DeployAccount` transactions are publicly visible in the mempool. Any observer can extract the sender address and immediately craft an `Invoke(nonce=1, invalid_sig)` for that address. No privileged access, special keys, or on-chain funds are required by the attacker. The race window is the entire time the `DeployAccount` sits in the mempool before being included in a block.

### Recommendation

Replace the coarse `account_tx_in_pool_or_recent_block` check with a check that specifically verifies a `DeployAccount` transaction exists in the pool for the sender address. The mempool should expose a dedicated query such as `deploy_account_in_pool(address) -> bool` that inspects transaction types, rather than returning `true` for any transaction. This mirrors the fix recommended in the original report: add access controls (type-specific checks) rather than relying on a weaker identity/state proxy.

### Proof of Concept

```
// Step 1: Alice submits a valid DeployAccount for address A.
//         The mempool accepts it; account_tx_in_pool_or_recent_block(A) → true.

// Step 2: Attacker observes A in the mempool and submits:
//   Invoke { sender_address: A, nonce: 1, signature: [0xdead, 0xbeef], ... }

// Step 3: Gateway stateful validation:
//   account_nonce(A) == 0  ✓
//   tx.nonce()       == 1  ✓
//   account_tx_in_pool_or_recent_block(A) == true  ✓  (due to Alice's DeployAccount)
//   → skip_validate = true → __validate__ NOT called → transaction admitted to mempool

// Step 4: Batcher builds block:
//   [DeployAccount(A)]  → succeeds, A deployed, nonce = 1
//   [Invoke(A, nonce=1, bad_sig)] → __validate__ called → REVERTED
//                                    nonce bumped to 2, fee charged

// Step 5: Alice's legitimate Invoke(A, nonce=1) → rejected: NonceTooOld
```

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L275-285)
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L995-997)
```rust
        if !self.execution_flags.validate {
            return Ok(None);
        }
```
