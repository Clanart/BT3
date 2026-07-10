### Title
Fee Permanently Lost When `ft_transfer_call` to NEAR Contract Destination Fails — (File: `near/omni-bridge/src/lib.rs`)

### Summary
When a cross-chain transfer targets a NEAR contract destination (i.e., `msg` is non-empty, triggering `ft_transfer_call`), and the destination contract rejects the tokens (returning `0` from `ft_on_transfer`), the `fin_transfer_send_tokens_callback` failure path burns/reverts the principal amount but silently discards both the token fee (`fee.fee`) and the native fee (`fee.native_fee`). Both fee components were already locked or burned on the source chain at initiation time and are irrecoverable.

### Finding Description

In `process_fin_transfer_to_near`, the bridge sends only `amount_without_fee` to the recipient contract via `ft_transfer_call`, then awaits the callback: [1](#0-0) 

In `fin_transfer_send_tokens_callback`, when `is_refund_required` returns `true` (the contract returned `0`, rejecting the tokens), the failure branch is: [2](#0-1) 

The branch:
1. Burns `amount_without_fee` (for deployed/bridged tokens).
2. Reverts lock actions for the **full** `transfer_message.amount` (restoring locked-token accounting).
3. Removes the fin-transfer record.

Neither `transfer_message.fee.fee` nor `transfer_message.fee.native_fee` is touched. Compare with the success branch, which explicitly mints/transfers both fee components to the relayer: [3](#0-2) 

`is_refund_required` confirms the trigger condition — a `0` return from `ft_on_transfer` (contract rejection): [4](#0-3) 

**What happens to the fee on the source chain?**

- **EVM source**: `nativeFee` ETH is sent as `msg.value` in `initTransfer` and held by the EVM bridge. There is no on-chain path to return it to the user after a NEAR-side failure. [5](#0-4) 

- **Solana source**: `native_fee` lamports are transferred to `sol_vault` at initiation and similarly have no recovery path. [6](#0-5) 

- **Token fee (`fee.fee`)**: For deployed (bridged) tokens, the fee was burned on the source chain but is never minted on NEAR. For native locked tokens, the fee portion remains locked in the bridge with no user-accessible withdrawal.

The `Fee` struct carries both components: [7](#0-6) 

### Impact Explanation

Both `fee.fee` and `fee.native_fee` are set by the user at initiation time on the source chain and are irrecoverably locked/burned there. When the NEAR-side `ft_transfer_call` fails, neither component is returned to the user nor awarded to the relayer — they are silently discarded. This is fee accounting corruption that permanently misdirects user value: the source-chain bridge holds ETH/SOL/tokens that can never be claimed by anyone. This matches the allowed impact: **High — fee/accounting corruption that misdirects value**.

### Likelihood Explanation

Any transfer to a NEAR contract destination where `ft_on_transfer` returns the full amount (contract paused, wrong message format, insufficient state, etc.) triggers this path. This is a realistic, user-reachable condition requiring no privileged access. The user controls the `msg` field and the destination contract, making this a normal operational scenario for cross-chain dApp integrations.

### Recommendation

In the `is_refund_required` branch of `fin_transfer_send_tokens_callback`, handle the fee components explicitly:

1. **Token fee (`fee.fee`)**: For deployed tokens, mint the fee to the sender (or burn it). For native locked tokens, transfer the fee back to the sender or keep it unlocked for a future claim.
2. **Native fee (`fee.native_fee`)**: Emit a `FailedFinTransferEvent` that includes the native fee amount, enabling a corresponding proof-based refund on the source chain, or mint the wrapped native token to the original sender on NEAR.

At minimum, the `FailedFinTransferEvent` should carry the fee fields so off-chain tooling can detect and surface the loss to users.

### Proof of Concept

1. User calls `initTransfer` on EVM with `amount = 1000`, `fee = 10`, `nativeFee = 0.01 ETH`, `message = "<contract_msg>"`. ETH and tokens are locked/burned on EVM.
2. Relayer calls `fin_transfer` on NEAR. `fin_transfer_callback` constructs a `TransferMessage` with `fee.fee = 10` and `fee.native_fee = 0.01 ETH` (in yoctoNEAR-equivalent wrapped units).
3. `process_fin_transfer_to_near` calls `send_tokens(token, recipient_contract, 990, "<contract_msg>")` via `ft_transfer_call`.
4. The recipient contract's `ft_on_transfer` returns `U128(0)` (rejects all tokens).
5. `fin_transfer_send_tokens_callback` enters the `is_refund_required == true` branch: burns `990` tokens, reverts lock actions for `1000`, removes the fin-transfer record. **Neither `fee.fee = 10` nor `fee.native_fee` is processed.**
6. The `FailedFinTransferEvent` is emitted with no fee refund information.
7. The user's 0.01 ETH `nativeFee` remains locked in the EVM bridge forever; the `fee.fee = 10` token units are permanently unaccounted for.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1702-1718)
```rust
        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
```

**File:** near/omni-bridge/src/lib.rs (L1720-1743)
```rust
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```

**File:** near/omni-bridge/src/lib.rs (L1784-1804)
```rust
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-413)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L75-86)
```rust
        if payload.native_fee > 0 {
            transfer(
                CpiContext::new(
                    self.common.system_program.to_account_info(),
                    Transfer {
                        from: self.user.to_account_info(),
                        to: self.sol_vault.to_account_info(),
                    },
                ),
                payload.native_fee,
            )?;
        }
```

**File:** near/omni-types/src/lib.rs (L537-548)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Fee {
    pub fee: U128,
    pub native_fee: U128,
}

impl Fee {
    pub const fn is_zero(&self) -> bool {
        self.fee.0 == 0 && self.native_fee.0 == 0
    }
}
```
