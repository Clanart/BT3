### Title
Locked-token over-count in `process_fin_transfer_to_other_chain` when a fast transfer is finalized — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When a cross-chain fast transfer is finalized via `process_fin_transfer_to_other_chain`, the `locked_tokens` accounting for the destination chain is inflated by `amount_without_fee` for every such transfer. The relayer is paid `amount_without_fee` on NEAR, but the corresponding decrement of `locked_tokens[destination_chain, token]` is never performed. Over time this phantom balance allows more tokens to be unlocked from NEAR than are actually held, breaking bridge collateralization.

---

### Finding Description

**Fast-transfer initiation — `fast_fin_transfer_to_other_chain` (lib.rs:914–972)**

When a relayer fronts tokens on the destination chain (e.g. Solana), the bridge records the obligation:

```rust
self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

self.lock_tokens_if_needed(
    fast_transfer.get_destination_chain(),   // e.g. Sol
    &fast_transfer.token_id,
    amount_without_fee,                      // ← locked for Sol
);
```

After this call: `locked_tokens[(Sol, token)] += amount_without_fee`. [1](#0-0) 

**Fast-transfer finalization — `process_fin_transfer_to_other_chain` (lib.rs:1980–2054)**

When the origin-chain proof arrives, the function:

1. Unlocks the full amount from the origin chain (correct).
2. Locks only the `fee` portion for the destination chain.
3. Pays the relayer `amount_without_fee` on NEAR via `send_tokens`.
4. **Never calls `unlock_tokens_if_needed(destination_chain, token, amount_without_fee)`.**

```rust
self.unlock_tokens_if_needed(
    transfer_message.get_origin_chain(),
    &token,
    transfer_message.amount.0,          // full amount unlocked from origin ✓
);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token,
    transfer_message.fee.fee.into(),    // only fee locked for destination
);

// fast transfer branch:
self.send_tokens(token, relayer, amount_without_fee, "").detach();
// ← amount_without_fee locked for destination in fast_fin_transfer_to_other_chain
//   is NEVER unlocked here
``` [2](#0-1) 

**Net accounting error per fast transfer:**

| Map key | Expected after finalization | Actual after finalization |
|---|---|---|
| `locked_tokens[(origin, token)]` | decremented by `full_amount` | decremented by `full_amount` ✓ |
| `locked_tokens[(dest, token)]` | `fee` only | `amount_without_fee + fee = full_amount` ✗ |

The phantom `amount_without_fee` in `locked_tokens[(dest, token)]` accumulates with every fast transfer finalization.

`send_tokens` (lib.rs:2056–2118) only mints/transfers tokens; it never touches `locked_tokens`. [3](#0-2) 

The `lock_tokens` / `unlock_tokens` primitives are straightforward checked arithmetic on the map: [4](#0-3) 

---

### Impact Explanation

`locked_tokens[(dest, token)]` is the collateral counter that guards how many tokens can be unlocked from NEAR for transfers originating on the destination chain. When it is inflated, `unlock_tokens_if_needed` succeeds for amounts that exceed the real backing. Early redeemers drain the actual token balance; later redeemers hit `ERR_INSUFFICIENT_LOCKED_TOKENS` or receive unbacked minted tokens, breaking bridge collateralization.

This matches the **High** impact class: *Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.*

---

### Likelihood Explanation

Every fast transfer that routes through a foreign-to-foreign path (e.g. Eth → Sol) and is subsequently finalized triggers the bug. Fast transfers are a core, incentivized relayer operation. The inflation is permanent and cumulative — it cannot be self-correcting.

---

### Recommendation

In `process_fin_transfer_to_other_chain`, when a fast transfer is found and the relayer is paid on NEAR, add the missing unlock for the destination chain before (or instead of) paying the relayer:

```rust
// Fast transfer branch — relayer is reimbursed on NEAR, so the
// amount_without_fee that was locked for the destination chain must be released.
self.unlock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token,
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
);
self.send_tokens(token, relayer, U128(amount_without_fee), "").detach();
self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

Alternatively, do not call `lock_tokens_if_needed` for `amount_without_fee` in `fast_fin_transfer_to_other_chain` at all, since the relayer will be reimbursed on NEAR rather than on the destination chain.

---

### Proof of Concept

**Setup:** Token is native to Ethereum. `locked_tokens[(Eth, token)] = 1000`. `locked_tokens[(Sol, token)] = 0`.

1. Relayer calls `fast_fin_transfer` for a transfer of 100 tokens (fee = 5) from Eth to Sol.
   - `fast_fin_transfer_to_other_chain` runs.
   - `locked_tokens[(Sol, token)] += 95` → **95**.

2. Relayer calls `fin_transfer` with the Eth proof.
   - `process_fin_transfer_to_other_chain` runs.
   - `locked_tokens[(Eth, token)] -= 100` → **900**.
   - `locked_tokens[(Sol, token)] += 5` → **100** (should be **5**).
   - Relayer receives 95 tokens on NEAR.

3. `locked_tokens[(Sol, token)]` is now **100** but only **5** tokens are actually owed to Sol (the fee). The phantom **95** allows a future Sol→NEAR transfer of 100 tokens to succeed, unlocking 100 tokens from NEAR even though only 5 are legitimately backed.

4. Repeat step 1–2 N times: phantom balance grows by `95 × N`, eventually allowing an attacker to drain the NEAR bridge contract of real tokens.

### Citations

**File:** near/omni-bridge/src/lib.rs (L932-938)
```rust
        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

        self.lock_tokens_if_needed(
            fast_transfer.get_destination_chain(),
            &fast_transfer.token_id,
            amount_without_fee,
        );
```

**File:** near/omni-bridge/src/lib.rs (L1997-2040)
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

            None
        };

        // If fast transfer happened, send tokens to the relayer that executed fast transfer
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

**File:** near/omni-bridge/src/lib.rs (L2056-2062)
```rust
    fn send_tokens(
        &self,
        token: AccountId,
        recipient: AccountId,
        amount: U128,
        msg: &str,
    ) -> Promise {
```

**File:** near/omni-bridge/src/token_lock.rs (L47-94)
```rust
impl Contract {
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
