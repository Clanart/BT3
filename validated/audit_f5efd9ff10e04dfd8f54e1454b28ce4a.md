### Title
Missing Migration of `relayers` and `relayer_config` State Permanently Locks Relayer Stakes and Blocks Fast Transfers - (File: `near/omni-bridge/src/migrate.rs`)

### Summary

The `migrate()` function in `near/omni-bridge/src/migrate.rs` reads `OldState` which explicitly includes `relayers: LookupMap<AccountId, RelayerState>` and `relayer_config: RelayerConfig`, but constructs the new `Contract` without carrying either field over. After migration, all previously registered trusted relayers lose their status, their staked NEAR is permanently locked with no recovery path, and all fast-transfer and relayer-gated bridge functions are blocked until relayers re-register from scratch.

### Finding Description

`OldState` declares two fields that track the trusted-relayer subsystem: [1](#0-0) 

The `migrate()` function constructs `Self { ... }` with explicit field assignments for every other field, but silently omits both: [2](#0-1) 

The current `Contract` struct does not declare `relayers` or `relayer_config` as explicit fields — they are managed by the `#[trusted_relayer]` proc-macro applied to the `impl` block: [3](#0-2) 

Because the migration does not transfer `old_state.relayers` into the new contract's storage layout, the macro's `is_trusted_relayer` lookup finds no registered relayers after the upgrade. Every relayer who staked NEAR and was previously active is silently erased.

### Impact Explanation

**Permanent freezing of relayer stakes (Critical):** Relayers stake NEAR tokens (e.g. 1,000 NEAR per the default config) to become trusted. Their stake is held in the contract's balance and tracked via `RelayerState`. After migration, the `RelayerState` records are gone. Relayers cannot call `resign_trusted_relayer` to recover their stake because the contract no longer recognises them as active relayers. The staked NEAR is permanently locked inside the contract with no recovery path.

**Fast-transfer and bridge-relay functions permanently blocked (High):** `fast_fin_transfer` enforces `require!(self.is_trusted_relayer(&signer_id), "Relayer is not active")`: [4](#0-3) 

`fin_transfer` and `sign_transfer` are also gated by the `#[trusted_relayer]` guard: [5](#0-4) [6](#0-5) 

With no trusted relayers after migration, all cross-chain fast-finalization and standard finalization flows are blocked until relayers re-register and wait out the `waiting_period_ns` again — but their original stakes are unrecoverable.

### Likelihood Explanation

The `migrate()` function is a privileged, one-time upgrade path. It is triggered by the DAO/deployer during a contract upgrade. The bug fires deterministically every time this migration is executed: there is no conditional path that preserves relayer state. Any deployment that uses this migration function will lose all relayer state.

### Recommendation

Carry `old_state.relayers` and `old_state.relayer_config` into the new contract state during migration, analogous to how every other `LookupMap` field is transferred. If the `#[trusted_relayer]` macro uses a different internal storage key than the old struct field, an explicit re-keying step is needed to copy entries from the old prefix to the new one. Additionally, add a post-migration check that verifies at least one trusted relayer exists before the upgrade is considered complete.

### Proof of Concept

1. Pre-migration: relayer `alice.near` stakes 1,000 NEAR via `apply_for_trusted_relayer`, waits out `waiting_period_ns`, and becomes a trusted relayer. Her `RelayerState` is stored in `old_state.relayers`.
2. DAO calls `migrate()`. The function reads `OldState` successfully but constructs `Self { ... }` without `relayers: old_state.relayers` or `relayer_config: old_state.relayer_config`.
3. Post-migration: `alice.near` calls `resign_trusted_relayer` to recover her 1,000 NEAR stake — the call fails because the contract no longer has her `RelayerState`.
4. `alice.near` attempts `fast_fin_transfer` via `ft_transfer_call` with a `FastFinTransferMsg` — the call panics at `require!(self.is_trusted_relayer(&signer_id), "Relayer is not active")`.
5. The 1,000 NEAR is permanently locked in the contract. All fast-transfer flows are blocked until new relayers re-register from scratch. [7](#0-6)

### Citations

**File:** near/omni-bridge/src/migrate.rs (L19-78)
```rust
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
pub struct OldState {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
    pub relayers: LookupMap<AccountId, RelayerState>,
    pub relayer_config: RelayerConfig,
}

#[near]
impl Contract {
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        if let Some(old_state) = env::state_read::<OldState>() {
            Self {
                factories: old_state.factories,
                pending_transfers: old_state.pending_transfers,
                finalised_transfers: old_state.finalised_transfers,
                finalised_utxo_transfers: old_state.finalised_utxo_transfers,
                fast_transfers: old_state.fast_transfers,
                token_id_to_address: old_state.token_id_to_address,
                token_address_to_id: old_state.token_address_to_id,
                token_decimals: old_state.token_decimals,
                deployed_tokens: old_state.deployed_tokens,
                deployed_tokens_v2: LookupMap::new(StorageKey::DeployedTokensV2),
                token_deployer_accounts: old_state.token_deployer_accounts,
                mpc_signer: old_state.mpc_signer,
                current_origin_nonce: old_state.current_origin_nonce,
                destination_nonces: old_state.destination_nonces,
                accounts_balances: old_state.accounts_balances,
                wnear_account_id: old_state.wnear_account_id,
                provers: old_state.provers,
                init_transfer_promises: old_state.init_transfer_promises,
                utxo_chain_connectors: old_state.utxo_chain_connectors,
                migrated_tokens: old_state.migrated_tokens,
                locked_tokens: old_state.locked_tokens,
            }
        } else {
            env::panic_str("Old state not found. Migration is not needed.")
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L245-249)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** near/omni-bridge/src/lib.rs (L445-447)
```rust
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
```

**File:** near/omni-bridge/src/lib.rs (L671-673)
```rust
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L756-756)
```rust
        require!(self.is_trusted_relayer(&signer_id), "Relayer is not active");
```
