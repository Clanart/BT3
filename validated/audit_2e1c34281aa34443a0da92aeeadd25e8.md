### Title
Bridge Controller Cannot Force-Upgrade Deployed OmniTokens, Invalidating Emergency Upgrade Safeguard — (File: `near/omni-bridge/src/lib.rs`, `near/omni-token/src/migrate.rs`)

---

### Summary

The Omni Bridge contract is set as the `controller` of every `OmniToken` it deploys. `OmniToken.upgrade_and_migrate()` is exclusively gated by `assert_controller()`, meaning only the bridge can trigger it. However, the bridge contract exposes **no method** that calls `upgrade_and_migrate()` on a deployed token. The `TokenUpgrader` role and the `ext_upgrade_and_migrate` external-contract trait both exist as design artifacts, but no bridge function wires them together. As a result, neither the DAO nor any privileged role can force-upgrade a deployed token in an emergency, permanently invalidating the upgrade safeguard.

---

### Finding Description

**Step 1 — Token deployment sets the bridge as controller.**

`TokenDeployer.deploy_token()` initialises every new `OmniToken` with `controller: env::predecessor_account_id()`. [1](#0-0) 

The bridge calls `ext_deployer::ext(deployer).deploy_token(...)`, so `env::predecessor_account_id()` inside the deployer is the bridge account. Every deployed token therefore has the bridge as its sole controller.

**Step 2 — `upgrade_and_migrate` is exclusively controller-gated.**

`OmniToken.upgrade_and_migrate()` calls `self.assert_controller()` as its first action. [2](#0-1) 

`assert_controller` enforces `env::predecessor_account_id() == self.controller`. [3](#0-2) 

No other account can call this function.

**Step 3 — The bridge has no code path to call `upgrade_and_migrate`.**

The `ext_upgrade_and_migrate` cross-contract trait is defined in `omni_ft.rs`, signalling design intent for the bridge to call it: [4](#0-3) 

The bridge also declares a `Role::TokenUpgrader` role, further indicating the upgrade path was planned: [5](#0-4) 

However, the bridge's `ext_token` external-contract trait — the interface through which the bridge calls deployed tokens — contains `mint`, `burn`, `set_metadata`, and storage functions, but **not** `upgrade_and_migrate`: [6](#0-5) 

A grep across all of `near/omni-bridge/src/` for `upgrade_and_migrate`, `TokenUpgrader`, `upgrade_token`, and `update_controller` returns a single match — the role definition at line 126 — confirming no bridge method invokes `upgrade_and_migrate` on a deployed token.

**Step 4 — No alternative escape hatch exists.**

`OmniToken.attach_full_access_key()` is also controller-gated and would allow adding a key to the token account for a manual upgrade, but the bridge equally has no method to call it: [7](#0-6) 

There is no `set_controller` function in `OmniToken`, so the controller cannot be changed to a human-controlled account either.

---

### Impact Explanation

If a deployed `OmniToken` contains a critical bug that freezes user balances or prevents withdrawals, the bridge — as the sole entity authorised to call `upgrade_and_migrate` — has no on-chain method to trigger the upgrade. User funds held in that token contract become permanently irrecoverable. This matches the allowed impact: **Critical — permanent freezing / irrecoverable lock of user funds in token flows**.

---

### Likelihood Explanation

Low-to-medium. Token bugs are uncommon but not impossible, especially given the migration complexity visible in `migrate.rs` (multiple state versions, PoA migration path). The `TokenUpgrader` role and `ext_upgrade_and_migrate` trait both exist precisely because the protocol designers anticipated needing this path. The gap between design intent and implementation makes the safeguard silently absent rather than explicitly disabled.

---

### Recommendation

Add a privileged method to the bridge contract, gated by `Role::TokenUpgrader` or `Role::DAO`, that calls `upgrade_and_migrate` on a specified deployed token:

```rust
#[access_control_any(roles(Role::TokenUpgrader, Role::DAO))]
pub fn upgrade_deployed_token(&self, token_id: AccountId) -> Promise {
    require!(self.is_deployed_token(&token_id), "Not a deployed token");
    ext_upgrade_and_migrate::ext(token_id)
        .with_static_gas(/* appropriate gas */)
        .upgrade_and_migrate()
}
```

This mirrors the pattern already used for `set_metadata` calls on deployed tokens and closes the gap between the declared `TokenUpgrader` role and its missing implementation.

---

### Proof of Concept

1. Bridge deploys token `usdc.token-deployer.bridge.near` via `deploy_token_callback` → `deploy_token_internal` → `ext_deployer::deploy_token`. Token is initialised with `controller = bridge.near`.
2. A bug in the token's `ft_transfer` logic causes all transfers to revert, locking user balances.
3. DAO identifies the bug and prepares a patched WASM.
4. DAO attempts to trigger an upgrade: searches the bridge for any method accepting a token address and new code/hash — none exists.
5. DAO cannot call `upgrade_and_migrate` directly on the token because `assert_controller` rejects any caller that is not `bridge.near`.
6. No `set_controller` exists to reassign control to a human-operated account.
7. User funds in the token are permanently frozen with no recovery path.

### Citations

**File:** near/token-deployer/src/lib.rs (L65-68)
```rust
            .function_call(
                "new".to_string(),
                json!({"controller": env::predecessor_account_id(), "metadata": metadata})
                    .to_string()
```

**File:** near/omni-token/src/migrate.rs (L76-77)
```rust
    fn upgrade_and_migrate(&self) {
        self.assert_controller();
```

**File:** near/omni-token/src/lib.rs (L82-85)
```rust
    pub fn attach_full_access_key(&mut self, public_key: PublicKey) -> Promise {
        self.assert_controller();
        Promise::new(env::current_account_id()).add_full_access_key(public_key)
    }
```

**File:** near/omni-token/src/lib.rs (L98-104)
```rust
    fn assert_controller(&self) {
        let caller = env::predecessor_account_id();
        require!(
            caller == self.controller,
            TokenError::MissingPermission.as_ref()
        );
    }
```

**File:** near/omni-token/src/omni_ft.rs (L32-35)
```rust
#[ext_contract(ext_upgrade_and_migrate)]
pub trait UpgradeAndMigrate {
    fn upgrade_and_migrate(&self);
}
```

**File:** near/omni-bridge/src/lib.rs (L126-126)
```rust
    TokenUpgrader,
```

**File:** near/omni-bridge/src/lib.rs (L131-171)
```rust
#[ext_contract(ext_token)]
pub trait ExtToken {
    fn ft_transfer(
        &mut self,
        receiver_id: AccountId,
        amount: U128,
        memo: Option<String>,
    ) -> PromiseOrValue<U128>;

    fn ft_transfer_call(
        &mut self,
        receiver_id: AccountId,
        amount: U128,
        memo: Option<String>,
        msg: String,
    ) -> PromiseOrValue<U128>;

    fn ft_metadata(&self) -> FungibleTokenMetadata;

    fn storage_deposit(
        &mut self,
        account_id: &AccountId,
        registration_only: Option<bool>,
    ) -> Option<StorageBalance>;

    fn storage_balance_of(&mut self, account_id: &AccountId) -> Option<StorageBalance>;

    fn mint(&mut self, account_id: AccountId, amount: U128, msg: Option<String>);

    fn burn(&mut self, amount: U128);

    fn set_metadata(
        &mut self,
        name: Option<String>,
        symbol: Option<String>,
        reference: Option<String>,
        reference_hash: Option<Base64VecU8>,
        decimals: Option<u8>,
        icon: Option<String>,
    );
}
```
