### Title
User Funds Permanently Locked When `normalize_amount` Returns Zero Due to Small Transfer Amount - (File: `near/omni-bridge/src/lib.rs`)

### Summary
When a user initiates a NEAR-side transfer for a token whose `origin_decimals > decimals` (e.g., NEAR with 24 decimals bridged to an EVM chain with 18 decimals), if the transferred amount minus fee is smaller than the normalization divisor (`10^(origin_decimals - decimals)`), `normalize_amount` returns zero via floor division. The subsequent `sign_transfer` call always panics with `InvalidAmountToTransfer`, permanently locking the user's tokens in the bridge with no recovery path.

### Finding Description

`normalize_amount` performs floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

In `sign_transfer`, this is applied to `amount_without_fee()` and the result is required to be non-zero:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

However, `init_transfer` only validates `fee < amount`, not that `normalize_amount(amount - fee) > 0`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [3](#0-2) 

This means a user can successfully lock tokens in the bridge with an amount that will always cause every future `sign_transfer` call to revert. The `TransferMessage` is stored in contract state and there is no cancel or refund function to recover the locked tokens. The `sign_transfer_callback` only removes the transfer message on a successful MPC signing response, which is never reached: [4](#0-3) 

The SECURITY.md comment at line 2781–2783 acknowledges that "dust stays locked/burned" when `fee = 0`, but this refers to sub-unit remainders, not to the entire transferred amount being below the normalization threshold. [5](#0-4) 

### Impact Explanation
**Critical — Permanent freezing of user funds.** Any user who initiates a transfer with an amount below `10^(origin_decimals - decimals)` has their tokens permanently locked in the NEAR bridge contract. For a token with `origin_decimals = 24` and `decimals = 18` (a common pairing for NEAR-native tokens bridged to EVM), the threshold is `1,000,000` base units. Any transfer of fewer than `1,000,000` units (minus fee) is irrecoverable. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation
**Medium.** The condition requires a token registered with `origin_decimals > decimals` (standard for NEAR tokens bridged to EVM chains) and a user sending a small amount below the normalization threshold. Both conditions are realistic in normal protocol usage. The `init_transfer` entry point is fully permissionless — any token holder can trigger this.

### Recommendation
Add a normalization check inside `init_transfer` (before storing the `TransferMessage`) to reject transfers whose net amount normalizes to zero:

```rust
let token_address = self.get_token_address(destination_chain, token_id);
if let Some(decimals) = self.token_decimals.get(&token_address) {
    let net = transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee);
    require!(
        Self::normalize_amount(net, decimals) > 0,
        BridgeError::InvalidAmountToTransfer.as_ref()
    );
}
```

Alternatively, add a `cancel_transfer` function that allows the original sender to reclaim locked tokens when `sign_transfer` has never succeeded.

### Proof of Concept

1. A token is registered with `origin_decimals = 24`, `decimals = 18` (normalization divisor = `10^6 = 1,000,000`).
2. User calls `ft_transfer_call` with `amount = 500,000` and `fee = 0`.
3. `init_transfer` passes the fee check (`0 < 500,000`) and stores the `TransferMessage`; tokens are locked.
4. Relayer calls `sign_transfer`:
   - `amount_without_fee() = 500,000`
   - `normalize_amount(500_000, Decimals { origin_decimals: 24, decimals: 18 }) = 500_000 / 1_000_000 = 0`
   - `require!(0 > 0, ...)` → panics with `InvalidAmountToTransfer`
5. Every subsequent `sign_transfer` call for this transfer ID panics identically.
6. No cancel or refund path exists; the 500,000 tokens are permanently locked in the bridge.

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

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L648-667)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
```

**File:** near/omni-bridge/src/lib.rs (L2781-2787)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
