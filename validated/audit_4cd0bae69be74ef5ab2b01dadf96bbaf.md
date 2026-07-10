### Title
Admin `set_locked_tokens` Overwrites Accumulated State, Enabling Accounting Corruption via Race Condition — (`File: near/omni-bridge/src/token_lock.rs`)

---

### Summary

The `set_locked_tokens` function in the NEAR bridge contract directly overwrites the `locked_tokens` accounting map with an absolute value, while all normal bridge operations use relative increment/decrement (`lock_tokens`, `unlock_tokens`). When governance calls `set_locked_tokens` to correct the locked balance, concurrent user bridge activity can race against it, leaving the final `locked_tokens` value inconsistent with the actual on-chain state. This breaks bridge collateralization accounting and can cause legitimate `unlock_tokens` calls to revert with `InsufficientLockedTokens`, permanently freezing user funds.

---

### Finding Description

`locked_tokens` is the bridge's per-chain, per-token collateral ledger. It is initialized to `0` when a token is bound via `bind_token_callback`, then incremented by `lock_tokens` on every outbound fast-transfer and decremented by `unlock_tokens` on every inbound finalization. The guard in `unlock_tokens` enforces that the ledger never goes negative:

```rust
// token_lock.rs:81-83
require!(
    available >= amount,
    TokenLockError::InsufficientLockedTokens.as_ref()
);
```

The privileged `set_locked_tokens` function, callable by `Role::DAO` or `Role::TokenLockController`, bypasses this increment/decrement discipline entirely and writes an arbitrary absolute value:

```rust
// token_lock.rs:38-44
#[access_control_any(roles(Role::DAO, Role::TokenLockController))]
pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
    for arg in args {
        self.locked_tokens
            .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
    }
}
```

Because NEAR transactions are ordered but not atomic with respect to concurrent user calls, the following race is possible:

**Scenario — locked_tokens set too low, funds frozen:**

1. `locked_tokens[Eth][token]` = 1 000 (1 000 tokens locked on Ethereum).
2. Governance submits `set_locked_tokens` with `amount = 500` (intending to correct an accounting discrepancy).
3. Before that transaction lands, a user submits a fast-transfer to Ethereum that calls `lock_tokens_if_needed(+600)`, making the ledger `1 600`.
4. Governance's `set_locked_tokens` executes and overwrites to `500`.
5. Ledger is now `500`, but `600` tokens were just locked in step 3.
6. When those 600 tokens are bridged back, `unlock_tokens(600)` panics: `500 < 600` → `InsufficientLockedTokens`.
7. The inbound `fin_transfer` reverts; the user's funds are permanently unclaimable.

**Scenario — locked_tokens set too high, collateral invariant broken:**

1. `locked_tokens[Eth][token]` = 1 000.
2. A user front-runs governance's `set_locked_tokens(amount=1 500)` by calling `unlock_tokens_if_needed(−300)`, making the ledger `700`.
3. Governance's `set_locked_tokens` executes and overwrites to `1 500`.
4. User back-runs by locking `300` more, making the ledger `1 800`.
5. The bridge now believes `1 800` tokens are locked on Ethereum, but only `1 300` actually are. The ledger overstates collateral by `500`, breaking the bridge's collateralization invariant.

---

### Impact Explanation

- **Permanent fund freeze (Critical):** If `set_locked_tokens` races with a concurrent `lock_tokens` call and the resulting ledger is lower than the actual locked amount, subsequent `unlock_tokens` calls for those tokens will always revert. The affected users cannot bridge their assets back; funds are irrecoverably locked.
- **Collateral accounting corruption (High):** If the ledger is set higher than the actual locked amount, the bridge's collateral invariant is broken. The bridge will permit `unlock_tokens` to succeed for amounts that were never actually locked, enabling unbacked token supply on the destination chain.

Both impacts fall within the allowed scope: "Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds" and "Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization."

---

### Likelihood Explanation

- Governance is expected to call `set_locked_tokens` during migrations, incident response, or manual corrections — the function exists precisely for operational use.
- NEAR's transaction ordering is deterministic but not serialized against concurrent user calls; any user monitoring the mempool (or simply submitting transfers normally) can race against a governance correction.
- No special attacker capability is required beyond normal bridge usage (`ft_transfer_call` → `init_transfer` or `fast_fin_transfer`).
- The external report's analogous finding was confirmed Medium even though it required a privileged governance call, because auditors cannot assume the function will only be used in a single safe way.

---

### Recommendation

Replace the absolute-set pattern with relative adjustment functions:

```rust
pub fn increase_locked_tokens(&mut self, args: Vec<AdjustLockedTokenArgs>) {
    for arg in args {
        let key = (arg.chain_kind, arg.token_id);
        let current = self.locked_tokens.get(&key).unwrap_or(0);
        self.locked_tokens.insert(&key, &current.saturating_add(arg.amount.0));
    }
}

pub fn decrease_locked_tokens(&mut self, args: Vec<AdjustLockedTokenArgs>) {
    for arg in args {
        let key = (arg.chain_kind, arg.token_id);
        let current = self.locked_tokens.get(&key).unwrap_or(0);
        require!(current >= arg.amount.0, TokenLockError::InsufficientLockedTokens.as_ref());
        self.locked_tokens.insert(&key, &(current - arg.amount.0));
    }
}
```

If an absolute override is ever truly necessary (e.g., emergency reset), it should be paired with a bridge pause to prevent concurrent user activity during the correction window.

---

### Proof of Concept

```
// Pseudocode — NEAR transaction ordering

// State: locked_tokens[(Eth, token)] = 1_000

// TX A (user): ft_transfer_call → fast_fin_transfer_to_other_chain
//   → lock_tokens_if_needed(Eth, token, 600)
//   → locked_tokens[(Eth, token)] = 1_600

// TX B (governance, lands after TX A):
//   set_locked_tokens([{chain_kind: Eth, token_id: token, amount: 500}])
//   → locked_tokens[(Eth, token)] = 500   ← overwrites 1_600

// TX C (user, bridging back the 600 locked in TX A):
//   fin_transfer → unlock_tokens(Eth, token, 600)
//   → require!(500 >= 600)  →  PANIC: InsufficientLockedTokens
//   → fin_transfer reverts; user's 600 tokens are permanently frozen
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** near/omni-bridge/src/token_lock.rs (L48-69)
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
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L71-94)
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
    }
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
