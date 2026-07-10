### Title
No Way to Update or Remove a UTXO Chain Connector Once Assigned — (`near/omni-bridge/src/btc.rs`)

### Summary

`add_utxo_chain_connector` permanently registers a UTXO chain connector (e.g., BTC connector) and its associated token mapping. Due to `add_token`'s `is_none()` guards, calling `add_utxo_chain_connector` a second time for the same chain panics with `TokenExists`. There is no `remove_utxo_chain_connector` function anywhere in the codebase. Once set, the connector is irremovable and irreplaceable without a full contract migration.

### Finding Description

`add_utxo_chain_connector` in `near/omni-bridge/src/btc.rs` performs two writes:

1. It calls `self.add_token(...)`, which inserts into `token_id_to_address`, `token_address_to_id`, and `token_decimals` — each guarded by `.is_none()` checks that panic with `BridgeError::TokenExists` if the key already exists.
2. It inserts into `self.utxo_chain_connectors` (a `HashMap`), which would succeed on a second call — but execution never reaches this line because `add_token` panics first. [1](#0-0) 

The `add_token` helper enforces strict one-time insertion: [2](#0-1) 

There is no `remove_utxo_chain_connector` function anywhere in the contract: [3](#0-2) 

(Compare: `remove_prover` exists for provers, but no equivalent exists for UTXO connectors or factories.)

The contract state confirms `utxo_chain_connectors` is a plain `HashMap<ChainKind, UTXOChainConfig>` with no privileged removal path: [4](#0-3) 

### Impact Explanation

If the registered UTXO connector contract (e.g., the BTC connector) is exploited through a bug in its own code, the DAO has no on-chain mechanism to replace it. All BTC bridge operations — `submit_transfer_to_utxo_chain_connector` and `rbf_increase_gas_fee` — are permanently routed through the compromised connector: [5](#0-4) 

This results in **permanent freezing or irrecoverable loss of user BTC funds** locked in the bridge flow, matching the Critical impact class: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

The only recovery path is a full contract upgrade with a custom migration that manually removes the token mappings — a complex, time-sensitive operation that may not be feasible during an active exploit.

### Likelihood Explanation

UTXO connector contracts are non-trivial smart contracts handling BTC custody logic. A bug in the connector (e.g., in `ft_on_transfer` handling, withdrawal logic, or access control) could allow an attacker to drain BTC tokens transferred via `ft_transfer_call`. The inability to replace the connector transforms a recoverable incident into a permanent protocol failure. Likelihood is **Medium** — the connector must first be compromised, but the inability to respond elevates the severity significantly.

### Recommendation

1. Add a privileged `remove_utxo_chain_connector(chain_kind: ChainKind)` function (DAO-only) that removes the connector from `utxo_chain_connectors` and cleans up the associated token mappings (`token_id_to_address`, `token_address_to_id`, `token_decimals`).
2. Add an `update_utxo_chain_connector(chain_kind, new_connector_id)` function that allows replacing only the connector address without touching the token mapping, for cases where only the connector contract needs to be swapped.
3. Analogously, audit `add_factory` (no `remove_factory`) and `add_token_deployer` (no `remove_token_deployer`) for the same class of issue, though those are less severe since `LookupMap::insert` allows overwriting without panicking.

### Proof of Concept

1. DAO calls `add_utxo_chain_connector(ChainKind::Btc, btc_connector_v1, nbtc_token, 8)` — succeeds, registers connector and token mappings.
2. `btc_connector_v1` is later found to have a critical bug.
3. DAO attempts `add_utxo_chain_connector(ChainKind::Btc, btc_connector_v2, nbtc_token, 8)` — **panics** at `add_token` with `ERR_TOKEN_EXISTS` because `token_id_to_address` already contains the `(ChainKind::Btc, nbtc_token)` key.
4. DAO attempts `add_utxo_chain_connector(ChainKind::Btc, btc_connector_v2, nbtc_token_v2, 8)` — **panics** at `add_token` because `token_address_to_id` already contains the BTC native token address.
5. No `remove_utxo_chain_connector` exists. The compromised `btc_connector_v1` remains the permanent, irremovable handler for all BTC bridge operations. [6](#0-5)

### Citations

**File:** near/omni-bridge/src/btc.rs (L88-101)
```rust
        ext_token::ext(btc_account_id)
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(FT_TRANSFER_CALL_GAS)
            .ft_transfer_call(self.get_utxo_chain_connector(chain_kind), amount, None, msg)
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SUBMIT_TRANSFER_TO_BTC_CONNECTOR_CALLBACK_GAS)
                    .submit_transfer_to_btc_connector_callback(
                        transfer.message,
                        transfer.owner,
                        fee_recipient,
                    ),
            )
    }
```

**File:** near/omni-bridge/src/btc.rs (L130-166)
```rust
    pub fn add_utxo_chain_connector(
        &mut self,
        chain_kind: ChainKind,
        utxo_chain_connector_id: AccountId,
        utxo_chain_token_id: AccountId,
        decimals: u8,
    ) {
        let storage_usage = env::storage_usage();
        let token_address = get_native_token_address(chain_kind)
            .near_expect(BridgeError::FailedToGetNativeTokenAddress);

        self.add_token(&utxo_chain_token_id, &token_address, decimals, decimals);

        self.utxo_chain_connectors.insert(
            chain_kind,
            UTXOChainConfig {
                connector: utxo_chain_connector_id,
                token_id: utxo_chain_token_id.clone(),
            },
        );

        let required_deposit = NEP141_DEPOSIT.saturating_add(
            env::storage_byte_cost()
                .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into()),
        );

        require!(
            env::attached_deposit() >= required_deposit,
            BridgeError::InsufficientStorageDeposit.as_ref()
        );

        ext_token::ext(utxo_chain_token_id)
            .with_static_gas(STORAGE_DEPOSIT_GAS)
            .with_attached_deposit(NEP141_DEPOSIT)
            .storage_deposit(&env::current_account_id(), Some(true))
            .detach();
    }
```

**File:** near/omni-bridge/src/lib.rs (L240-242)
```rust
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
```

**File:** near/omni-bridge/src/lib.rs (L1749-1757)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn add_prover(&mut self, chain: ChainKind, account_id: AccountId) {
        self.provers.insert(&chain, &account_id);
    }

    #[access_control_any(roles(Role::DAO))]
    pub fn remove_prover(&mut self, chain: ChainKind) {
        self.provers.remove(&chain);
    }
```

**File:** near/omni-bridge/src/lib.rs (L2704-2736)
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
        require!(
            self.token_address_to_id
                .insert(token_address, token_id)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
    }
```
