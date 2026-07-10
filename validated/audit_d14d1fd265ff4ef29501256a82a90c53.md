Now I have all the code I need to make a definitive assessment. Let me trace the full execution path.

**Key facts established:**

1. `migrate()` resets `deployed_tokens_v2` to a fresh empty `LookupMap` while preserving `deployed_tokens` intact. [1](#0-0) 

2. `get_token_origin_chain` checks `deployed_tokens_v2` first (miss post-migration), then UTXO chain (miss for non-UTXO), then falls to the name-matching heuristic for any token in `deployed_tokens`. If the name matches no prefix, it calls `env::panic_str`. [2](#0-1) 

3. Both `lock_tokens_if_needed` and `unlock_tokens_if_needed` call `get_token_origin_chain` unconditionally. [3](#0-2) 

4. `init_transfer_internal` calls `lock_tokens_if_needed`. [4](#0-3) 

5. `process_fin_transfer_to_other_chain` calls both `unlock_tokens_if_needed` and `lock_tokens_if_needed`. [5](#0-4) 

6. `send_fee_internal` (called during `fin_transfer` to NEAR) calls `unlock_tokens_if_needed`. [6](#0-5) 

7. `add_deployed_tokens` (DAO-only) inserts tokens into both `deployed_tokens` AND `deployed_tokens_v2` with arbitrary `token_id` values. After migration, `deployed_tokens_v2` is reset to empty, so these tokens survive only in `deployed_tokens`. [7](#0-6) 

**Race condition claim:** NEAR's execution model is single-threaded per contract call — there is no concurrent execution within a single contract. The race condition scenario described is not applicable.

**Core panic claim:** This IS real. The `OldState` already contains `deployed_tokens_v2`, meaning the migration discards previously-correct chain-kind data and relies entirely on the name heuristic to reconstruct it lazily. Any token in `deployed_tokens` whose name doesn't match the recognized prefixes (`eth`, `factory.bridge.near`, `base`, `arb`, `bnb`, `pol`, `hlevm`, `abs`, `sol`, `fogo`, `strk`/`starknet`) will cause a deterministic panic on every subsequent transfer attempt.

**Realistic precondition:** `add_deployed_tokens` is DAO-only but accepts arbitrary `token_id` values. Tokens like `aurora`, `usdc.near`, or any custom-named token added via this path would be in `deployed_tokens` with a correct entry in `deployed_tokens_v2` in the old state — but after migration, only in `deployed_tokens`. The heuristic would panic for them.

**Impact on `fin_transfer`:** If `get_token_origin_chain` panics during `process_fin_transfer_to_other_chain` or `send_fee_internal`, the finalization of an incoming cross-chain transfer fails deterministically. Since the user already locked/burned funds on the source chain, this constitutes permanent freezing of user funds — the relayer cannot retry successfully because the panic is deterministic.

---

### Title
Post-Migration Permanent DoS via `CannotDetermineOriginChain` Panic for Non-Standard Token Names — (`near/omni-bridge/src/migrate.rs` + `near/omni-bridge/src/lib.rs`)

### Summary
`migrate()` resets `deployed_tokens_v2` to an empty map while preserving `deployed_tokens`. `get_token_origin_chain` falls back to a name-prefix heuristic for tokens in `deployed_tokens` but absent from `deployed_tokens_v2`. Any token whose account ID does not match a recognized prefix causes an unconditional `env::panic_str(CannotDetermineOriginChain)`, permanently blocking all transfer operations for that token.

### Finding Description
`migrate()` constructs the new state with `deployed_tokens_v2: LookupMap::new(StorageKey::DeployedTokensV2)`, discarding all previously stored chain-kind mappings. [8](#0-7) 

`get_token_origin_chain` then falls through to a name-matching heuristic for any token present in `deployed_tokens` but absent from `deployed_tokens_v2`. The wildcard arm panics:

```rust
_ => env::panic_str(&BridgeError::CannotDetermineOriginChain.as_ref()),
``` [9](#0-8) 

`add_deployed_tokens` (DAO-only) inserts tokens into both maps simultaneously. After migration, `deployed_tokens_v2` is wiped, leaving these tokens only in `deployed_tokens`. If any such token has a name not matching the heuristic (e.g., `aurora`, `usdc.near`), every call to `lock_tokens_if_needed` or `unlock_tokens_if_needed` for that token panics. [10](#0-9) 

### Impact Explanation
- **`init_transfer`**: panics in `lock_tokens_if_needed`; the `ft_transfer_call` callback returns the full amount, so user funds are refunded. No fund loss, but the bridge is permanently unusable for this token.
- **`fin_transfer` (incoming cross-chain transfer)**: panics in `unlock_tokens_if_needed` / `lock_tokens_if_needed` inside `process_fin_transfer_to_other_chain` or `send_fee_internal`. The user already locked/burned funds on the source chain. Since the panic is deterministic, finalization can never succeed — **user funds are permanently frozen**. [11](#0-10) [6](#0-5) 

### Likelihood Explanation
The precondition — a token with a non-standard name in `deployed_tokens` — is set by the DAO via `add_deployed_tokens`. Any token added with a name not matching the heuristic's prefixes (e.g., `aurora`, `usdc.near`, or any custom-named token) triggers the bug after migration. The migration itself is a privileged operation, but once executed, any unprivileged user attempting to bridge an affected token triggers the permanent DoS. The heuristic covers only tokens deployed via `deploy_token_internal` (which generates prefixed names); it does not cover the full set of names that `add_deployed_tokens` permits.

### Recommendation
Do not reset `deployed_tokens_v2` in `migrate()`. Instead, carry it forward from `old_state.deployed_tokens_v2`. If a fresh reset is intentional, populate `deployed_tokens_v2` eagerly during migration by iterating `deployed_tokens` and applying the heuristic — or change the wildcard arm to return `ChainKind::Near` as a safe default rather than panicking, and add a DAO-callable repair function to correct any misclassified entries.

### Proof of Concept
```rust
// Unit test (no privileged setup needed beyond DAO-equivalent state manipulation)
#[test]
#[should_panic(expected = "ERR_CANNOT_DETERMINE_ORIGIN_CHAIN")]
fn test_non_standard_token_panics_after_migration() {
    let mut contract = get_default_contract();
    // Simulate a token added via add_deployed_tokens with a non-standard name
    // (deployed_tokens_v2 is empty, as it would be post-migrate())
    let token_id: AccountId = "aurora".parse().unwrap();
    contract.deployed_tokens.insert(&token_id);
    // deployed_tokens_v2 intentionally NOT populated (mirrors post-migration state)

    // Any user calling ft_transfer_call for this token triggers:
    // init_transfer → init_transfer_internal → lock_tokens_if_needed → get_token_origin_chain → panic
    contract.get_token_origin_chain(&token_id);
}
```

### Citations

**File:** near/omni-bridge/src/migrate.rs (L50-78)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1414-1451)
```rust
    pub fn get_token_origin_chain(&mut self, token: &AccountId) -> ChainKind {
        if let Some(origin_chain) = self.deployed_tokens_v2.get(token) {
            return origin_chain;
        }

        if let Some(origin_chain) = self.get_utxo_chain_by_token(token) {
            return origin_chain;
        }

        if !self.deployed_tokens.contains(token) {
            return ChainKind::Near;
        }

        let origin_chain = match token.as_str() {
            s if s.starts_with("eth")
                || s.contains("factory.bridge.near")
                || s.contains("factory.sepolia.testnet") =>
            {
                ChainKind::Eth
            }
            s if s.starts_with("base") => ChainKind::Base,
            s if s.starts_with("arb") => ChainKind::Arb,
            s if s.starts_with("bnb") => ChainKind::Bnb,
            s if s.starts_with("pol") => ChainKind::Pol,
            s if s.starts_with("hlevm") => ChainKind::HyperEvm,
            s if s.starts_with("abs") => ChainKind::Abs,
            s if s.starts_with("sol") => ChainKind::Sol,
            s if s.starts_with("fogo") => ChainKind::Fogo,
            s if s.starts_with("strk") || s.starts_with("starknet") => ChainKind::Strk,
            _ => env::panic_str(&BridgeError::CannotDetermineOriginChain.as_ref()),
        };

        if !origin_chain.is_utxo_chain() {
            self.deployed_tokens_v2.insert(token, &origin_chain);
        }

        origin_chain
    }
```

**File:** near/omni-bridge/src/lib.rs (L1534-1545)
```rust
    pub fn add_deployed_tokens(&mut self, tokens: Vec<AddDeployedTokenArgs>) {
        require!(
            env::attached_deposit()
                >= NEP141_DEPOSIT
                    .saturating_mul(tokens.len().try_into().near_expect(BridgeError::Cast)),
            BridgeError::NotEnoughAttachedDeposit.as_ref()
        );

        for token_info in tokens {
            self.deployed_tokens.insert(&token_info.token_id);
            self.deployed_tokens_v2
                .insert(&token_info.token_id, &token_info.token_address.get_chain());
```

**File:** near/omni-bridge/src/lib.rs (L1853-1857)
```rust
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L1997-2022)
```rust
        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );
```

**File:** near/omni-bridge/src/lib.rs (L2684-2684)
```rust
        self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);
```

**File:** near/omni-bridge/src/token_lock.rs (L96-120)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }

    pub(crate) fn unlock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.unlock_tokens(chain_kind, token_id, amount)
    }
```
