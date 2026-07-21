### Title
Signature Verification Bypass via `skip_stateful_validations` Allows Invalid Invoke Transactions to Evict Valid Ones from Mempool — (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The `skip_stateful_validations` function in the gateway's stateful validator skips the `__validate__` entry point (signature verification) for Invoke transactions with nonce=1 when a `DeployAccount` for the same address is present in the mempool. An unprivileged attacker who observes a victim's pending `DeployAccount` transaction can submit an Invoke transaction with an **invalid signature** that bypasses this check and is admitted to the mempool. With fee escalation enabled by default, the attacker can then evict the victim's valid Invoke transaction from the mempool by offering a higher tip, causing the victim's transaction to be permanently lost.

---

### Finding Description

The gateway's `skip_stateful_validations` function implements a UX feature: when a user submits a `DeployAccount` + `Invoke` pair simultaneously, the Invoke (nonce=1) is admitted without running `__validate__` because the account does not exist yet in state. [1](#0-0) 

The condition for skipping is:
1. The transaction is an Invoke with `tx.nonce() == 1`
2. The on-chain account nonce is `0` (account not yet deployed)
3. `account_tx_in_pool_or_recent_block(sender_address)` returns `true` [2](#0-1) 

The third condition is satisfied whenever **any** transaction from that address is in the mempool pool or was recently committed — it does not verify that the existing transaction is specifically a `DeployAccount`: [3](#0-2) 

When `skip_validate = true`, `run_validate_entry_point` sets `validate: false` in `ExecutionFlags`, meaning the `__validate__` entry point (which performs signature verification) is never called at gateway admission time: [4](#0-3) 

The production default configuration has `enable_fee_escalation: true`: [5](#0-4) 

The mempool's `handle_fee_escalation` function removes an existing transaction for the same `(address, nonce)` pair when the incoming transaction offers a sufficiently higher tip and max gas price: [6](#0-5) 

**Attack sequence:**

1. Victim submits `DeployAccount` (nonce=0) and `Invoke` (nonce=1) for address `A`. Both are admitted to the mempool.
2. Attacker observes the victim's `DeployAccount` in the mempool (public pending state).
3. Attacker crafts an `Invoke` with `sender_address=A`, `nonce=1`, **invalid/arbitrary signature**, and a tip exceeding the victim's by the escalation threshold (default 10%).
4. Gateway evaluates: `account_nonce=0`, `tx.nonce()=1`, `account_tx_in_pool_or_recent_block(A)=true` → `skip_validate=true` → `__validate__` is **not called**. The invalid Invoke passes all other checks (nonce range, resource bounds, duplicate hash).
5. The mempool's `add_tx_validations` calls `handle_fee_escalation`, which removes the victim's valid Invoke and inserts the attacker's invalid one.
6. During block execution: `DeployAccount` succeeds, the attacker's Invoke fails `__validate__` (invalid signature), and the victim's Invoke is permanently gone from the mempool.

---

### Impact Explanation

This matches **"High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

- An invalid transaction (carrying an invalid signature) is admitted to the mempool, bypassing the only gateway-level signature check.
- A valid transaction is evicted from the mempool via fee escalation with the invalid one.
- The victim's `Invoke` is permanently lost; the victim must resubmit and pay again.
- The attacker's invalid Invoke fails during block execution (no fee charged from the non-existent account), making this a zero-cost griefing attack.
- The attack can be repeated for every new `DeployAccount` transaction observed in the mempool, enabling systematic DoS of account deployment flows.

---

### Likelihood Explanation

- The mempool is a public data structure; pending transactions are observable via RPC (`starknet_getTransactionStatus`, mempool snapshots, P2P propagation).
- Fee escalation is enabled by default in production configuration.
- The attacker requires no special privileges — only the ability to submit transactions to the gateway.
- The `skip_stateful_validations` path is exercised in the normal `deploy_account + invoke` UX flow, making it a reachable production code path.

---

### Recommendation

1. **Restrict the skip condition to verified `DeployAccount` transactions**: Instead of checking `account_tx_in_pool_or_recent_block` (which matches any transaction type), check specifically that a `DeployAccount` transaction for the sender address is present in the mempool pool.

2. **Alternatively, perform a lightweight signature pre-check**: Even when skipping the full `__validate__` execution, verify that the transaction signature is structurally valid (correct length, non-zero values) before admission.

3. **Decouple skip-validate from fee escalation eligibility**: Transactions admitted with `skip_validate=true` should not be eligible to replace transactions that were admitted with full validation.

---

### Proof of Concept

```
// Setup: victim submits DeployAccount + Invoke for address A
victim_deploy = DeployAccount { sender: A, nonce: 0, sig: valid_sig }
victim_invoke = Invoke { sender: A, nonce: 1, tip: 100, sig: valid_sig }
gateway.add_tx(victim_deploy)  // admitted, enters mempool pool
gateway.add_tx(victim_invoke)  // admitted via skip_validate (deploy in pool)

// Attacker observes victim_deploy in mempool
attacker_invoke = Invoke {
    sender: A,
    nonce: 1,
    tip: 111,              // > 100 * 1.10 = 110, satisfies fee_escalation_percentage=10
    max_l2_gas_price: ..., // also escalated
    sig: [0x1337, 0x1337]  // INVALID signature
}

// Gateway evaluation:
//   account_nonce(A) = 0  (not deployed yet)
//   tx.nonce() = 1
//   account_tx_in_pool_or_recent_block(A) = true  (victim_deploy is in pool)
//   => skip_validate = true => __validate__ NOT called
//   => attacker_invoke admitted to mempool

// Mempool fee escalation:
//   handle_fee_escalation removes victim_invoke (tip=100)
//   inserts attacker_invoke (tip=111, invalid sig)

// Block execution:
//   victim_deploy executes: account A deployed
//   attacker_invoke executes: __validate__ called → FAILS (invalid sig) → tx rejected
//   victim_invoke: GONE from mempool, never executed
``` [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L279-286)
```rust
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };

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

**File:** crates/apollo_mempool/src/mempool.rs (L658-661)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L724-768)
```rust
    fn handle_fee_escalation(
        &mut self,
        incoming_tx_reference: TransactionReference,
        validation_only: bool,
    ) -> MempoolResult<()> {
        let TransactionReference { address, nonce, .. } = incoming_tx_reference;

        self.validate_no_delayed_declare_front_run(incoming_tx_reference)?;

        if !self.config.static_config.enable_fee_escalation {
            if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
                return Err(MempoolError::DuplicateNonce { address, nonce });
            };

            return Ok(());
        }

        let Some(existing_tx_reference) = self.tx_pool.get_by_address_and_nonce(address, nonce)
        else {
            // Replacement irrelevant: no existing transaction with the same nonce for address.
            return Ok(());
        };

        if !self.should_replace_tx(&existing_tx_reference, &incoming_tx_reference) {
            info!(
                "{existing_tx_reference} was not replaced by {incoming_tx_reference} due to \
                 insufficient fee escalation."
            );
            // TODO(Elin): consider adding a more specific error type / message.
            return Err(MempoolError::DuplicateNonce { address, nonce });
        }

        if validation_only {
            return Ok(());
        }

        debug!("{existing_tx_reference} will be replaced by {incoming_tx_reference}.");

        self.tx_queue.remove_txs(&[existing_tx_reference]);
        self.tx_pool
            .remove(existing_tx_reference.tx_hash)
            .expect("Transaction hash from pool must exist.");

        Ok(())
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L3107-3115)
```json
  "mempool_config.static_config.enable_fee_escalation": {
    "description": "If true, transactions can be replaced with higher fee transactions.",
    "privacy": "Public",
    "value": true
  },
  "mempool_config.static_config.fee_escalation_percentage": {
    "description": "Percentage increase for tip and max gas price to enable transaction replacement.",
    "privacy": "Public",
    "value": 10
```
