### Title
Premature `DeployTokenEvent` Emission Before Deployer Cross-Contract Call Completes Creates Irrecoverable Token-Mapping Desync — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

In `deploy_token_internal`, the `DeployTokenEvent` log is emitted and all token-mapping state is written **before** the cross-contract call to the `TokenDeployer` contract completes. If the deployer call fails, `deploy_token_by_deployer_callback` rolls back the on-chain state, but the emitted event log is permanent and cannot be reverted. Off-chain systems (relayers, indexers, bridge SDK) that consume `DeployTokenEvent` to determine token availability will permanently believe the token is deployed on NEAR, while the on-chain registry has no record of it. Any user who subsequently initiates a cross-chain transfer for that token will have their funds locked in the source-chain bridge with no path to finalization.

---

### Finding Description

In `deploy_token_internal` (lines 2413–2454 of `near/omni-bridge/src/lib.rs`):

1. Token mappings are written into `token_id_to_address`, `token_address_to_id`, `token_decimals`, `deployed_tokens`, and `deployed_tokens_v2`.
2. `DeployTokenEvent` is emitted via `env::log_str`.
3. Only then is the cross-contract call `ext_deployer::ext(deployer).deploy_token(...)` dispatched. [1](#0-0) 

In `deploy_token_by_deployer_callback` (lines 1177–1198), if the deployer call fails, the state is rolled back: [2](#0-1) 

However, the `DeployTokenEvent` emitted in step 2 is a NEAR receipt log — it is committed to the blockchain at the moment `deploy_token_callback` executes and **cannot be reverted** by any subsequent callback, regardless of whether the deployer call succeeds or fails.

The `TokenDeployer.deploy_token` call can fail in realistic conditions:

- The `global_code_hash` stored in `TokenDeployer` is stale (e.g., the global contract was updated but `set_global_code_hash` was not called), causing `use_global_contract` to reference a non-existent hash.
- The target sub-account already exists on NEAR (e.g., pre-created by any party), causing `create_account` to fail.
- `DEPLOY_TOKEN_GAS` is insufficient for the full promise chain inside `TokenDeployer.deploy_token`. [3](#0-2) 

The `DeployTokenEvent` type is defined as a permanent on-chain signal consumed by off-chain infrastructure: [4](#0-3) 

A secondary issue exists in the success path: when the deployer call succeeds, `storage_deposit` is dispatched as a fire-and-forget promise with no failure callback: [5](#0-4) 

If `storage_deposit` fails, the token is registered as deployed but the bridge contract has no storage slot on the token. The `mint` path for `ft_transfer_call` (message-bearing transfers) calls `internal_deposit` on the bridge itself before forwarding, which will panic if the bridge has no storage: [6](#0-5) 

---

### Impact Explanation

**High — token-mapping corruption that misdirects value and breaks bridge collateralization.**

When `DeployTokenEvent` is emitted but the deployer call subsequently fails:

1. Off-chain relayers and the bridge SDK observe the event and mark the NEAR token as live.
2. Users on EVM (or other chains) call `initTransfer`, locking or burning their tokens in the source-chain bridge.
3. Relayers call `fin_transfer` on NEAR; the call fails with `TokenNotRegistered` because the state was rolled back.
4. The source-chain bridge has no refund path — the locked/burned tokens are irrecoverable until the token is re-deployed via a separate DAO action.

If the DAO is slow to respond or the `global_code_hash` cannot be updated (e.g., the global contract account is deleted), the user funds are permanently frozen in the source-chain bridge.

The secondary `storage_deposit` fire-and-forget failure causes all message-bearing `fin_transfer` calls for the affected token to permanently revert, freezing any funds routed through the `ft_transfer_call` path.

---

### Likelihood Explanation

**Low-Medium.** The deployer call failure requires either:

- A stale `global_code_hash` in `TokenDeployer` — a realistic operational hazard when the global omni-token contract is upgraded without updating the deployer (directly analogous to the external report's whitelist dependency misconfiguration).
- A pre-existing sub-account at the deterministic token address — achievable by any NEAR account that creates a named account before the bridge does, though sub-account creation requires the parent account's key.
- Insufficient `DEPLOY_TOKEN_GAS` — a code-level constant that could be too low after future gas schedule changes.

The `storage_deposit` failure is lower likelihood but requires no external trigger beyond a gas exhaustion edge case.

---

### Recommendation

1. **Move `DeployTokenEvent` emission into `deploy_token_by_deployer_callback`**, emitting it only in the success branch after the deployer call is confirmed successful. This ensures the on-chain event log is always consistent with the on-chain state.

2. **Add a failure callback for `storage_deposit`** in `deploy_token_by_deployer_callback`. If `storage_deposit` fails, roll back the token registration (same cleanup as the deployer-failure branch) and emit a `FailedDeployTokenEvent` so off-chain systems can react.

3. **Validate deployer readiness before dispatching**: check that `TokenDeployer.get_global_code_hash()` returns a non-zero hash before calling `deploy_token`, or add an on-chain invariant check.

---

### Proof of Concept

**Scenario A — stale `global_code_hash`:**

1. DAO deploys a new global omni-token contract but forgets to call `TokenDeployer.set_global_code_hash`.
2. Any user calls `deploy_token` on the NEAR bridge with a valid EVM `LogMetadata` proof.
3. `deploy_token_internal` writes all token mappings and emits `DeployTokenEvent` (permanent).
4. `ext_deployer.deploy_token` is called; `use_global_contract(stale_hash)` fails — no contract with that hash exists.
5. `deploy_token_by_deployer_callback` enters the `else` branch and removes all state entries.
6. On-chain state: token not registered. On-chain log: `DeployTokenEvent` present.
7. Bridge SDK and relayers observe the event; users on EVM call `initTransfer` for the token, locking funds.
8. Relayer calls `fin_transfer` on NEAR → panics with `TokenNotRegistered`.
9. User funds are locked on EVM with no finalization path until DAO manually re-deploys the token. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1177-1198)
```rust
    #[private]
    pub fn deploy_token_by_deployer_callback(
        &mut self,
        token_address: &OmniAddress,
        token_id: AccountId,
    ) -> PromiseOrValue<()> {
        if env::promise_result_checked(0, usize::MAX).is_ok() {
            ext_token::ext(token_id)
                .with_static_gas(STORAGE_DEPOSIT_GAS)
                .with_attached_deposit(NEP141_DEPOSIT)
                .storage_deposit(&env::current_account_id(), Some(true))
                .into()
        } else {
            self.deployed_tokens.remove(&token_id);
            self.deployed_tokens_v2.remove(&token_id);
            self.token_id_to_address
                .remove(&(token_address.get_chain(), token_id));
            self.token_address_to_id.remove(token_address);
            self.token_decimals.remove(token_address);
            PromiseOrValue::Value(())
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L2413-2454)
```rust
        let storage_usage = env::storage_usage();
        self.add_token(
            &token_id,
            token_address,
            metadata.decimals,
            metadata.decimals,
        );

        require!(
            self.deployed_tokens.insert(&token_id),
            BridgeError::TokenExists.as_ref()
        );
        self.deployed_tokens_v2
            .insert(&token_id, &token_address.get_chain());

        let required_deposit = env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
            .saturating_add(NEP141_DEPOSIT);

        require!(
            attached_deposit >= required_deposit,
            BridgeError::InsufficientStorageDeposit.as_ref()
        );

        env::log_str(
            &OmniBridgeEvent::DeployTokenEvent {
                token_id: token_id.clone(),
                token_address: token_address.clone(),
                metadata: metadata.clone(),
            }
            .to_log_string(),
        );

        ext_deployer::ext(deployer)
            .with_static_gas(DEPLOY_TOKEN_GAS)
            .with_attached_deposit(attached_deposit.saturating_sub(required_deposit))
            .deploy_token(token_id.clone(), metadata)
            .then(
                Self::ext(env::current_account_id())
                    .deploy_token_by_deployer_callback(token_address, token_id),
            )
    }
```

**File:** near/token-deployer/src/lib.rs (L58-73)
```rust
    #[payable]
    #[access_control_any(roles(Role::Controller, Role::LegacyController))]
    pub fn deploy_token(&mut self, account_id: AccountId, metadata: &BasicMetadata) -> Promise {
        Promise::new(account_id)
            .create_account()
            .transfer(env::attached_deposit())
            .use_global_contract(self.global_code_hash)
            .function_call(
                "new".to_string(),
                json!({"controller": env::predecessor_account_id(), "metadata": metadata})
                    .to_string()
                    .into_bytes(),
                NO_DEPOSIT,
                OMNI_TOKEN_INIT_GAS,
            )
    }
```

**File:** near/omni-types/src/near_events.rs (L37-41)
```rust
    DeployTokenEvent {
        token_id: AccountId,
        token_address: OmniAddress,
        metadata: BasicMetadata,
    },
```

**File:** near/omni-token/src/lib.rs (L135-139)
```rust
        if let Some(msg) = msg {
            self.token
                .internal_deposit(&env::predecessor_account_id(), amount.into());

            self.ft_transfer_call(account_id, amount, None, msg)
```
