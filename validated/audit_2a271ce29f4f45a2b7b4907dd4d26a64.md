### Title
`deploy_token` Has No Deposit Refund on Duplicate Concurrent Calls, Causing Permanent Loss of Caller's NEAR Deposit - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`deploy_token` is a permissionless, payable function that requires an attached NEAR deposit for storage. It performs an async proof verification before writing state. There is no duplicate-call guard before the async step and no refund callback after it. If two callers submit the same `LogMetadata` proof concurrently, both pass proof verification, but the second caller's `deploy_token_callback` panics with `ERR_TOKEN_EXISTS`. Because the original deposit was already transferred to the contract and no refund promise is chained, the second caller's NEAR deposit is permanently locked in the bridge contract.

### Finding Description

`deploy_token` is declared with only `#[payable]` and `#[pause(except(roles(Role::DAO)))]` — no `#[trusted_relayer]` restriction — making it callable by any account. [1](#0-0) 

The function immediately forwards to an async `verify_proof` call and chains a single callback:

```rust
pub fn deploy_token(&mut self, #[serializer(borsh)] args: DeployTokenArgs) -> Promise {
    self.verify_proof(args.chain_kind, args.prover_args).then(
        Self::ext(env::current_account_id())
            .with_attached_deposit(NO_DEPOSIT)
            .with_static_gas(DEPLOY_TOKEN_CALLBACK_GAS)
            .deploy_token_callback(near_sdk::env::attached_deposit()),
    )
}
```

There is **no pre-check** that the token is not already being deployed, and **no refund callback** is chained. Compare this with `bind_token`, which explicitly chains a `bind_token_refund` promise to return the deposit to the caller if `bind_token_callback` panics: [2](#0-1) 

`bind_token_refund` uses `call_result.unwrap_or_else(|_| env::attached_deposit())` to ensure the full deposit is returned on failure: [3](#0-2) 

`deploy_token` has no equivalent. When `deploy_token_callback` calls `deploy_token_internal`, the very first operation is `add_token`, which panics with `ERR_TOKEN_EXISTS` if the token mapping already exists: [4](#0-3) 

And `deployed_tokens.insert` also panics with `ERR_TOKEN_EXISTS` if the token was already inserted: [5](#0-4) 

When `deploy_token_callback` panics, the NEAR deposit that was transferred to the contract at the time of the original `deploy_token` call is not refunded — it is permanently absorbed into the contract's balance.

### Impact Explanation

Any user who calls `deploy_token` for a token that was concurrently deployed by another caller loses their entire attached NEAR deposit. The deposit covers storage for the token deployment plus `NEP141_DEPOSIT` and can be several NEAR tokens. There is no admin recovery path. This constitutes a **permanent, irrecoverable lock of user funds** in the bridge contract.

This matches the allowed impact: *Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

### Likelihood Explanation

`deploy_token` is permissionless and is called by relayers and users during the token-bridging setup flow. The window between the `verify_proof` call and the callback execution is several seconds on NEAR (cross-contract call latency). During the initial deployment of a new token, multiple independent relayers or users may submit the same `LogMetadata` proof simultaneously. A malicious actor can also deliberately front-run a legitimate caller to cause them to lose their deposit at zero cost to the attacker (the attacker's own call succeeds and their deposit is used legitimately).

### Recommendation

Chain a refund callback after `deploy_token_callback`, mirroring the pattern already used in `bind_token`:

```rust
pub fn deploy_token(&mut self, #[serializer(borsh)] args: DeployTokenArgs) -> Promise {
    self.verify_proof(args.chain_kind, args.prover_args)
        .then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(NO_DEPOSIT)
                .with_static_gas(DEPLOY_TOKEN_CALLBACK_GAS)
                .deploy_token_callback(near_sdk::env::attached_deposit()),
        )
        .then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(DEPLOY_TOKEN_REFUND_GAS)
                .deploy_token_refund(env::predecessor_account_id()),
        )
}
```

Where `deploy_token_refund` returns the unused deposit to the caller on failure, exactly as `bind_token_refund` does.

### Proof of Concept

1. Token `eth:0xABC` needs to be deployed on NEAR.
2. User A calls `deploy_token` with a valid `LogMetadata` proof for `eth:0xABC` and attaches 5 NEAR.
3. User B (or a malicious actor) calls `deploy_token` with the **same proof** and attaches 5 NEAR, before A's callback executes.
4. Both calls pass `verify_proof` (the `LogMetadata` prover has no used-proof registry).
5. User A's `deploy_token_callback` executes first: `add_token` and `deployed_tokens.insert` succeed; token is deployed.
6. User B's `deploy_token_callback` executes: `add_token` panics with `ERR_TOKEN_EXISTS`.
7. User B's 5 NEAR deposit remains in the bridge contract with no refund path. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1136-1145)
```rust
    #[payable]
    #[pause(except(roles(Role::DAO)))]
    pub fn deploy_token(&mut self, #[serializer(borsh)] args: DeployTokenArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(NO_DEPOSIT)
                .with_static_gas(DEPLOY_TOKEN_CALLBACK_GAS)
                .deploy_token_callback(near_sdk::env::attached_deposit()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1147-1175)
```rust
    #[private]
    pub fn deploy_token_callback(
        &mut self,
        attached_deposit: NearToken,
        #[callback_result]
        #[serializer(borsh)]
        call_result: Result<ProverResult, PromiseError>,
    ) -> Promise {
        let Ok(ProverResult::LogMetadata(metadata)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        let chain = metadata.emitter_address.get_chain();
        require!(
            self.factories.get(&chain) == Some(metadata.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        self.deploy_token_internal(
            chain,
            &metadata.token_address,
            BasicMetadata {
                name: metadata.name,
                symbol: metadata.symbol,
                decimals: metadata.decimals,
            },
            attached_deposit,
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1223-1239)
```rust
    #[payable]
    #[pause(except(roles(Role::DAO)))]
    pub fn bind_token(&mut self, #[serializer(borsh)] args: BindTokenArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args)
            .then(
                Self::ext(env::current_account_id())
                    .with_attached_deposit(NO_DEPOSIT)
                    .with_static_gas(BIND_TOKEN_CALLBACK_GAS)
                    .bind_token_callback(near_sdk::env::attached_deposit()),
            )
            .then(
                Self::ext(env::current_account_id())
                    .with_attached_deposit(env::attached_deposit())
                    .with_static_gas(BIND_TOKEN_REFUND_GAS)
                    .bind_token_refund(near_sdk::env::predecessor_account_id()),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1303-1312)
```rust
    #[private]
    #[payable]
    pub fn bind_token_refund(
        &mut self,
        predecessor_account_id: AccountId,
        #[callback_result] call_result: Result<NearToken, PromiseError>,
    ) {
        let refund_amount = call_result.unwrap_or_else(|_| env::attached_deposit());
        Self::refund(predecessor_account_id, refund_amount);
    }
```

**File:** near/omni-bridge/src/lib.rs (L2413-2424)
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
```

**File:** near/omni-bridge/src/lib.rs (L2704-2717)
```rust
    fn add_token(
        &mut self,
        token_id: &AccountId,
        token_address: &OmniAddress,
        decimals: u8,
        origin_decimals: u8,
    ) {
        let chain_kind = token_address.get_chain();
        require!(
            self.token_id_to_address
                .insert(&(chain_kind, token_id.clone()), token_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
```
