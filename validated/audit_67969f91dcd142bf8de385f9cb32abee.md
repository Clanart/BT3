### Title
`set_locked_tokens()` Overwrites Concurrent User-Initiated Lock Updates Without Old-Balance Check — (File: `near/omni-bridge/src/token_lock.rs`)

---

### Summary

`set_locked_tokens()` in `near/omni-bridge/src/token_lock.rs` allows a `DAO` or `TokenLockController` role to write an **absolute value** directly into the `locked_tokens` mapping without reading or validating the current value first. Because `lock_tokens()` and `unlock_tokens()` are also called by unprivileged user flows (`init_transfer`, `fin_transfer`), a user transaction that executes between the admin's off-chain read and on-chain write will have its update silently overwritten, corrupting the bridge's locked-token accounting.

---

### Finding Description

`set_locked_tokens` performs a raw `insert` with no old-value guard:

```rust
// near/omni-bridge/src/token_lock.rs  lines 38-44
#[access_control_any(roles(Role::DAO, Role::TokenLockController))]
pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
    for arg in args {
        self.locked_tokens
            .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
    }
}
``` [1](#0-0) 

The same `locked_tokens` map is modified by `lock_tokens` (increment) and `unlock_tokens` (decrement), which are called from user-facing entry points:

- `init_transfer_internal` → `lock_tokens_if_needed` → `lock_tokens` (increments on user bridge-out)
- `process_fin_transfer_to_near` → `unlock_tokens_if_needed` → `unlock_tokens` (decrements on user bridge-in) [2](#0-1) [3](#0-2) [4](#0-3) 

`unlock_tokens` enforces a collateralization invariant — it panics if the stored amount would go negative:

```rust
// near/omni-bridge/src/token_lock.rs  lines 81-83
require!(
    available >= amount,
    TokenLockError::InsufficientLockedTokens.as_ref()
);
``` [5](#0-4) 

Because NEAR includes multiple transactions per block and their ordering within a block is validator-determined, a user transaction can execute between the admin's off-chain observation and the admin's on-chain `set_locked_tokens` call, producing a stale overwrite.

---

### Impact Explanation

**Path A — Understated locked balance → permanent fund freeze (Critical)**

1. `locked_tokens[(Eth, usdc)] = 1000`.
2. Admin observes `1000`, decides to correct to `900`, submits `set_locked_tokens(amount=900)`.
3. Before the admin tx lands, a user's `init_transfer` executes: `lock_tokens(Eth, usdc, 100)` → stored value becomes `1100`.
4. Admin tx lands: `insert(900)` overwrites `1100`.
5. The 100 USDC the user just locked is now invisible to the accounting.
6. When the corresponding `fin_transfer` arrives and calls `unlock_tokens(100)`, if the running total has since dropped to `< 100`, the `require!(available >= amount)` check panics.
7. The user's `fin_transfer` is permanently blocked — funds are irrecoverably frozen in the bridge.

**Path B — Overstated locked balance → collateralization break (Critical)**

1. `locked_tokens[(Eth, usdc)] = 1000`.
2. Admin observes `1000`, decides to correct to `1100`, submits `set_locked_tokens(amount=1100)`.
3. Before the admin tx lands, a user's `fin_transfer` executes: `unlock_tokens(Eth, usdc, 100)` → stored value becomes `900`.
4. Admin tx lands: `insert(1100)` overwrites `900`.
5. The bridge now believes `1100` USDC is locked on Eth, but only `900` actually is.
6. Subsequent `fin_transfer` calls can unlock up to `1100` USDC worth of NEAR-side tokens against only `900` USDC of real Eth-side collateral — 200 USDC of minted/released tokens are unbacked, directly breaking bridge collateralization.

---

### Likelihood Explanation

`set_locked_tokens` is an operational correction tool that will be used whenever the admin detects an accounting discrepancy. Active bridges process user transactions continuously. The window between the admin's off-chain read and on-chain write is non-zero and grows with network congestion. No malicious intent is required from either party — the race is a structural property of the write-without-check pattern. Likelihood is **medium-high** during any period of active bridge usage combined with an admin correction.

---

### Recommendation

Add an `expected_amount` (old-balance) parameter to `SetLockedTokenArgs` and assert equality before writing:

```rust
pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
    for arg in args {
        let key = (arg.chain_kind, arg.token_id.clone());
        let current = self.locked_tokens.get(&key)
            .expect("token not registered");
        require!(
            current == arg.expected_amount.0,
            "set_locked_tokens: stale expected amount"
        );
        self.locked_tokens.insert(&key, &arg.amount.0);
    }
}
```

This is the exact mitigation recommended in the analogous `setPendingRedemptionBalance` report: require the caller to supply the value they observed off-chain and revert if it no longer matches.

---

### Proof of Concept

```
Block N-1:
  locked_tokens[(Eth, usdc.near)] = 1_000_000

Admin off-chain read: observes 1_000_000, prepares set_locked_tokens(amount=900_000)

Block N (transaction ordering within block):
  Tx 1 (user): init_transfer(usdc.near → Eth, amount=200_000)
               → lock_tokens(Eth, usdc.near, 200_000)
               → locked_tokens[(Eth, usdc.near)] = 1_200_000

  Tx 2 (admin): set_locked_tokens([{chain_kind: Eth, token_id: usdc.near, amount: 900_000}])
                → locked_tokens.insert((Eth, usdc.near), 900_000)
                → locked_tokens[(Eth, usdc.near)] = 900_000  ← overwrites 1_200_000

Result:
  200_000 USDC worth of user lock is erased from accounting.
  When the corresponding fin_transfer arrives and calls unlock_tokens(200_000),
  if running total < 200_000, the call panics with InsufficientLockedTokens.
  User funds are permanently frozen in the bridge.
```

### Citations

**File:** near/omni-bridge/src/token_lock.rs (L38-44)
```rust
    #[access_control_any(roles(Role::DAO, Role::TokenLockController))]
    pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
        for arg in args {
            self.locked_tokens
                .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
        }
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L48-68)
```rust
    fn lock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(current_amount) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        let new_amount = current_amount
            .checked_add(amount)
            .near_expect(TokenLockError::LockedTokensOverflow);

        self.locked_tokens.insert(&key, &new_amount);

        LockAction::Locked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
```

**File:** near/omni-bridge/src/token_lock.rs (L71-93)
```rust
    fn unlock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(available) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );

        let remaining = available - amount;
        self.locked_tokens.insert(&key, &remaining);

        LockAction::Unlocked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
```

**File:** near/omni-bridge/src/lib.rs (L1851-1857)
```rust
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L1881-1885)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```
