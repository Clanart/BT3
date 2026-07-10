### Title
Permanent Token Lock When Normalized Transfer Amount Rounds to Zero — (`near/omni-bridge/src/lib.rs`)

### Summary

A user can initiate a NEAR→EVM bridge transfer with an amount that, after decimal normalization, becomes zero. The tokens are burned/locked on NEAR and stored in `pending_transfers`, but `sign_transfer` will always revert with `InvalidAmountToTransfer` because the normalized amount is 0. No user-callable cancel mechanism exists for `pending_transfers`, resulting in permanent, irrecoverable fund loss.

---

### Finding Description

**Step 1 — Tokens burned/locked with no normalized-amount pre-check.**

When a user calls `ft_on_transfer` → `init_transfer` → `init_transfer_internal`, the only validation on the amount is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

There is no check that `normalize_amount(amount - fee, decimals) > 0`. If the check passes, `init_transfer_internal` immediately burns or locks the tokens and inserts the entry into `pending_transfers`:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
// ...
U128(0)   // ← 0 returned to ft_transfer_call = no refund
``` [2](#0-1) 

**Step 2 — `sign_transfer` permanently blocked.**

The relayer later calls `sign_transfer`, which computes the normalized amount and hard-requires it to be non-zero:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [3](#0-2) 

For any NEAR token with 24 origin decimals bridging to an EVM token with 6 decimals, the division factor is 10^18. Any transfer amount below 10^18 yoctoNEAR (< 1 NEAR) normalizes to 0. `sign_transfer` will **always** panic with `ERR_INVALID_AMOUNT_TO_TRANSFER` for such a transfer — the MPC call is never reached, the callback is never invoked, and the entry is never removed from `pending_transfers`.

**Step 3 — No cancel mechanism.**

`remove_transfer_message` is only called inside `sign_transfer_callback` (when fee is zero after a successful MPC signature) and `remove_transfer_message_without_refund` is only called inside `init_transfer_internal` on a storage-balance failure. There is no public function that allows a user to cancel a pending transfer and recover their tokens. [4](#0-3) 

The `pending_transfers` map and the `fast_transfers` map are the only state stores for in-flight transfers, and neither exposes a user-callable exit path. [5](#0-4) 

---

### Impact Explanation

- Tokens are burned (deployed bridge tokens) or locked (native NEAR tokens) atomically in `init_transfer_internal` with no refund.
- `sign_transfer` will revert on every subsequent call for the affected `TransferId`.
- No on-chain path exists for the user to recover the funds.
- Matches **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

- The NEAR protocol uses 24-decimal tokens; EVM tokens commonly use 6 or 8 decimals. The minimum transferable amount for a 24→6 decimal pair is 10^18 yoctoNEAR = 1 NEAR. Any user who transfers less than 1 NEAR triggers the lock.
- No UI or on-chain guard warns the user or rejects the transaction before tokens are burned.
- The `init_transfer` entry point is fully permissionless — any token holder can reach it.
- The Solana SECURITY.md already acknowledges an analogous "no validation of recipient" issue causing permanent lock, confirming the team is aware this class of lock exists but has not addressed the decimal-normalization variant on the NEAR side. [6](#0-5) 

---

### Recommendation

Add a normalized-amount pre-check inside `init_transfer` (before burning/locking) or at the top of `init_transfer_internal`:

```rust
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the existing guard in `sign_transfer` and prevents tokens from being burned/locked when the transfer can never be finalized.

---

### Proof of Concept

```
Setup:
  - NEAR token "token.near" with origin_decimals = 24
  - EVM bridge token with decimals = 6
  - Decimal factor = 10^18

Attack chain:
  1. User calls ft_transfer_call(
         receiver_id = bridge.near,
         amount      = 1,          // 1 yoctoNEAR
         msg         = InitTransfer{ recipient: EVM_ADDR, fee: 0, ... }
     )

  2. ft_on_transfer → init_transfer:
       require!(0 < 1)  ✅  fee check passes
       init_transfer_internal:
         burn_tokens_if_needed(token.near, 1)   ← tokens gone
         pending_transfers.insert(TransferId{Near, nonce=N}, ...)
         return U128(0)                          ← no refund to ft_transfer_call

  3. Relayer calls sign_transfer(TransferId{Near, N}, ...):
       amount_to_transfer = normalize_amount(1, decimals)
                          = 1 / 10^18 = 0
       require!(0 > 0, ERR_INVALID_AMOUNT_TO_TRANSFER)  ❌ PANIC

  4. Transfer stays in pending_transfers forever.
     User has no cancel function.
     1 yoctoNEAR is permanently lost.
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L222-223)
```rust
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
```

**File:** near/omni-bridge/src/lib.rs (L475-485)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1850-1864)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```

**File:** near/omni-bridge/src/lib.rs (L2194-2224)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }

    fn remove_transfer_message_without_refund(
        &mut self,
        transfer_id: TransferId,
    ) -> TransferMessage {
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        transfer.message
    }
```

**File:** solana/SECURITY.md (L17-17)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
```
