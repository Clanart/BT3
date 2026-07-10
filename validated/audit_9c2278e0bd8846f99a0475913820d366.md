### Title
Zero-Normalized Amount Causes Permanent Fund Lock Without State Cleanup — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a transfer via `init_transfer`, tokens are locked/burned and a `TransferMessage` is stored in `pending_transfers`. If the transferred amount (after fee deduction) normalizes to zero on the destination chain due to decimal truncation, the subsequent `sign_transfer` call panics via `require!(amount_to_transfer > 0, ...)` — but this panic does **not** clean up the already-committed state from `init_transfer`. The transfer message remains permanently in `pending_transfers` and the user's tokens remain permanently locked or burned with no recovery path.

---

### Finding Description

The vulnerability class is **callback/state desync from a zero-value revert without prior state cleanup** — the direct analog of the `hedgeDelta(0)` bug.

**Step 1 — `init_transfer_internal` commits state irreversibly:** [1](#0-0) 

`init_transfer_internal` calls `add_transfer_message` (storing the message in `pending_transfers`), then calls `burn_tokens_if_needed` or `lock_tokens_if_needed`. These are separate NEAR transactions; their state is committed on-chain before `sign_transfer` is ever called.

**Step 2 — `sign_transfer` normalizes the amount and panics if it is zero:** [2](#0-1) 

`normalize_amount` converts the NEAR-side amount (24 decimals) to the destination chain's decimal representation. For tokens with fewer decimals (e.g., 6-decimal USDC on EVM), any amount smaller than `10^(24-6) = 10^18` yocto-units rounds to zero. The `require!(amount_to_transfer > 0)` then panics, reverting the `sign_transfer` transaction.

**Step 3 — No cleanup occurs:**

The panic in `sign_transfer` reverts only that transaction. The `pending_transfers` entry and the locked/burned token balance from the earlier `init_transfer` transaction are **not** rolled back. There is no `cancel_transfer`, no refund path, and no mechanism for the user to recover their funds.

The only place `remove_transfer_message` is called is inside `sign_transfer_callback` when the fee is zero: [3](#0-2) 

But `sign_transfer_callback` is never reached because `sign_transfer` panics before the MPC signing promise is even created.

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of user funds.**

- For native NEAR tokens: `lock_tokens_if_needed` increments `locked_tokens` for the destination chain. The locked balance can never be decremented because `fin_transfer` on the destination side will never be called (no valid signature is ever produced), and there is no cancel path.
- For deployed bridge tokens: `burn_tokens_if_needed` destroys the tokens. They are gone permanently.
- The `pending_transfers` entry occupies storage indefinitely, consuming the user's storage deposit.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

**Medium.** The condition is triggered whenever:

1. A user sends a token amount (after fee) that is smaller than `10^(near_decimals - dest_decimals)` in yocto-units.
2. The destination chain token has fewer decimals than the NEAR representation (e.g., 6-decimal USDC, 8-decimal WBTC).

This is reachable by any unprivileged user calling `ft_transfer_call` on the NEAR bridge contract. No special role or privileged access is required. A user could accidentally trigger this by sending a dust amount, or a malicious actor could grief a specific user by front-running or social engineering them into sending a dust transfer.

---

### Recommendation

Mirror the fix from the external report: add an early-exit (or a revert with cleanup) **before** state is committed, not after. Specifically:

1. **In `init_transfer` / `init_transfer_internal`**: compute `normalize_amount` before locking/burning tokens and before storing the transfer message. If the normalized amount is zero, return the full token amount to the sender immediately (as a refund) without storing any state.
2. **Alternatively**, add a `cancel_transfer` function that allows the original sender to reclaim their locked tokens and remove the pending transfer message when `sign_transfer` is permanently blocked.

---

### Proof of Concept

1. A NEAR-side USDC token has 24 decimals on NEAR; the EVM-side USDC has 6 decimals. The normalization divisor is `10^(24-6) = 10^18`.
2. User calls `ft_transfer_call` on the NEAR USDC contract, transferring `500_000_000_000_000_000` yocto-USDC (0.5 × 10^18 units, i.e., less than 1 micro-USDC on EVM) with a fee of 0.
3. `init_transfer_internal` runs: the transfer message is stored in `pending_transfers`, and `lock_tokens_if_needed` increments `locked_tokens`. State is committed.
4. A relayer calls `sign_transfer(transfer_id, ...)`.
5. `normalize_amount(500_000_000_000_000_000, decimals)` returns `0` (integer division: `5×10^17 / 10^18 = 0`).
6. `require!(0 > 0, ...)` panics. The `sign_transfer` transaction reverts.
7. The transfer message remains in `pending_transfers`. The user's tokens remain locked. No recovery is possible. [2](#0-1) [4](#0-3)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

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
    }
```
