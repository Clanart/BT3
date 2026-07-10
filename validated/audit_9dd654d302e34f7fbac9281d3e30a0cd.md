### Title
`fast_fin_transfer_to_other_chain` Locks Only `amount_without_fee` But `claim_fee` Unlocks the Full Fee — Corrupting `locked_tokens` Accounting - (File: `near/omni-bridge/src/lib.rs`)

### Summary

In `fast_fin_transfer_to_other_chain`, the bridge locks only `amount_without_fee` on the destination chain, but the subsequently created `TransferMessage` records the **full** `amount` (including fee). When `claim_fee` is later called, it computes `fee = transfer_message.amount - denormalized_amount` and calls `unlock_tokens_if_needed(destination_chain, token, fee)`. Because the fee was never locked on the destination chain, this over-decrements `locked_tokens[destination_chain][token]` by `fee` for every fast transfer routed to a non-NEAR chain. The cumulative under-count breaks bridge collateralization tracking and can cause legitimate return-transfers to revert with `InsufficientLockedTokens`, permanently freezing user funds.

### Finding Description

**Normal transfer path** (`init_transfer_internal`, lines 1853–1857):

```rust
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,   // full amount (amount_without_fee + fee)
);
```

After `claim_fee` unlocks `fee`, the net locked on the destination chain is `amount_without_fee`. This is correct — the user's bridge tokens on the destination chain are fully backed.

**Fast-transfer path** (`fast_fin_transfer_to_other_chain`, lines 928–938):

```rust
let amount_without_fee = fast_transfer.amount_without_fee()...;

self.lock_tokens_if_needed(
    fast_transfer.get_destination_chain(),
    &fast_transfer.token_id,
    amount_without_fee,          // ← only amount_without_fee is locked
);
```

The `TransferMessage` stored in `pending_transfers` carries `amount: fast_transfer.amount` (the full amount). When `claim_fee_callback` runs (lines 1122–1133):

```rust
let denormalized_amount = Self::denormalize_amount(fin_transfer.amount.0, ...);
let fee = transfer_message.amount.0 - denormalized_amount;   // = fee portion
self.send_fee_internal(&transfer_message, fee_recipient, fee)
```

Inside `send_fee_internal` (line 2684):

```rust
self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);
```

This decrements `locked_tokens[destination_chain][token]` by `fee`, even though only `amount_without_fee` was ever added. The net change per fast transfer is:

```
locked[dest] += amount_without_fee   (fast_fin_transfer_to_other_chain)
locked[dest] -= fee                  (claim_fee)
─────────────────────────────────────────────────────────────────────
net: locked[dest] += amount_without_fee − fee   (= amount − 2·fee)
```

The correct net should be `+amount_without_fee` (matching the normal path). The discrepancy is `−fee` per fast transfer.

Compare with `process_fin_transfer_to_other_chain` (lines 2002–2022), which correctly locks **both** the fee and `amount_without_fee` on the destination chain:

```rust
self.lock_tokens_if_needed(destination_chain, &token, transfer_message.fee.fee.into());
// ...
self.lock_tokens_if_needed(destination_chain, &token, transfer_message.amount_without_fee()...);
```

`fast_fin_transfer_to_other_chain` omits the fee-locking step entirely.

### Impact Explanation

1. **Accounting corruption / under-collateralization**: `locked_tokens[destination_chain][token]` is under-counted by `fee` for every fast transfer to a non-NEAR chain. Over many transfers this accumulates, making the bridge appear less collateralized than it is.

2. **Permanent freezing of user funds**: When a user later returns their destination-chain bridge tokens to NEAR, `process_fin_transfer_to_near` calls `unlock_tokens_if_needed(origin_chain, token, transfer_message.amount.0)`. If the cumulative under-count has driven `locked_tokens[dest]` below the amount needed for that unlock, the call panics with `InsufficientLockedTokens`, making the return transfer permanently unexecutable and the user's funds irrecoverable.

This matches the allowed impact: **High — Balance/fee/accounting corruption that breaks bridge collateralization** and **Critical — Permanent freezing of user funds in bridge flows**.

### Likelihood Explanation

Any trusted relayer can call `fast_fin_transfer` (via `ft_on_transfer` with a `FastFinTransfer` message) routing a transfer to a non-NEAR destination chain. This is a normal, documented protocol operation. The accounting error occurs automatically on every such call. No special privileges beyond being a trusted relayer are required, and the relayer role is granted to active participants in the protocol.

### Recommendation

In `fast_fin_transfer_to_other_chain`, lock the **full** amount (including fee) on the destination chain, mirroring the pattern in `process_fin_transfer_to_other_chain`:

```rust
// Instead of:
self.lock_tokens_if_needed(
    fast_transfer.get_destination_chain(),
    &fast_transfer.token_id,
    amount_without_fee,
);

// Use:
self.lock_tokens_if_needed(
    fast_transfer.get_destination_chain(),
    &fast_transfer.token_id,
    fast_transfer.amount.0,   // full amount = amount_without_fee + fee
);
```

Alternatively, add a second `lock_tokens_if_needed` call for the fee portion, exactly as `process_fin_transfer_to_other_chain` does at lines 2002–2006.

### Proof of Concept

**Setup**: Token `T` is NEAR-native; destination chain is EVM. `locked_tokens[EVM][T] = 1000` (from prior transfers).

**Step 1 — Relayer calls `fast_fin_transfer` routing to EVM** with `amount = 500`, `fee = 100`:
- `amount_without_fee = 400`
- `lock_tokens_if_needed(EVM, T, 400)` → `locked[EVM][T] = 1400`
- `TransferMessage { amount: 500, fee: 100 }` stored in `pending_transfers`

**Step 2 — Transfer finalised on EVM**: user receives 400 T-EVM, relayer receives 100 T-EVM.

**Step 3 — Relayer calls `claim_fee` on NEAR** with proof that `fin_transfer.amount = 400`:
- `fee = 500 − 400 = 100`
- `unlock_tokens_if_needed(EVM, T, 100)` → `locked[EVM][T] = 1300`

**Expected** after steps 1–3: `locked[EVM][T] = 1400` (1000 prior + 400 new user tokens circulating).
**Actual**: `locked[EVM][T] = 1300` — under-counted by 100 (the fee).

**Step 4 — User returns 400 T-EVM to NEAR** (burns on EVM, calls `fin_transfer` on NEAR):
- `process_fin_transfer_to_near` calls `unlock_tokens_if_needed(EVM, T, 400)`
- If prior transfers have also been under-counted and `locked[EVM][T]` has drifted below 400, this panics with `InsufficientLockedTokens` → user's 400 T-EVM are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L928-938)
```rust
        let amount_without_fee = fast_transfer
            .amount_without_fee()
            .near_expect(BridgeError::InvalidFee);

        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

        self.lock_tokens_if_needed(
            fast_transfer.get_destination_chain(),
            &fast_transfer.token_id,
            amount_without_fee,
        );
```

**File:** near/omni-bridge/src/lib.rs (L1122-1133)
```rust
        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
```

**File:** near/omni-bridge/src/lib.rs (L1853-1857)
```rust
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L2002-2022)
```rust
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
