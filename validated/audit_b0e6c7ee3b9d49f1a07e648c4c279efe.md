### Title
Unchecked Fee Token Transfer via `.detach()` Silently Loses Fee Funds — (`near/omni-bridge/src/lib.rs`)

### Summary
In `fin_transfer_send_tokens_callback`, both the token-denominated fee transfer and the native-fee mint use `.detach()`, meaning the promise result is never checked. If either cross-contract call fails (e.g., the fee recipient has no storage deposit for the token), the failure is silently swallowed: the fee is neither delivered to the fee recipient nor recoverable, permanently misdirecting value.

### Finding Description
After the main recipient transfer succeeds, `fin_transfer_send_tokens_callback` dispatches fee payments using `.detach()`: [1](#0-0) 

For deployed (bridge-minted) tokens, `mint` is called with `.detach()` — if it fails, the fee amount is never minted to anyone; it is simply lost from the expected supply. [2](#0-1) 

For native (locked) tokens, `ft_transfer` is called with `.detach()` — if it fails, the fee tokens remain locked inside the bridge contract with no recovery path. [3](#0-2) 

The native-fee mint path has the same flaw: [4](#0-3) 

`.detach()` in NEAR SDK means the spawned promise is fire-and-forget: no callback is registered, no panic is propagated, and no state rollback occurs on failure. This is the direct NEAR analog of calling `token.transfer(...)` without checking the boolean return value.

### Impact Explanation
- **Deployed tokens**: The fee amount is never minted. The circulating supply of the bridge token is permanently less than the amount locked on the origin chain, corrupting bridge collateralization accounting.
- **Native tokens**: The fee tokens remain locked in the bridge contract indefinitely. They accumulate silently, inflating the bridge's apparent token balance relative to what was legitimately locked for pending transfers, corrupting the bridge's accounting of locked collateral.

Both outcomes match **High — Balance, fee, or accounting corruption that breaks bridge collateralization or misdirects value**.

### Likelihood Explanation
The trigger condition is a failed `ft_transfer` or `mint` cross-contract call. On NEAR, `ft_transfer` panics (and thus the detached promise fails) if the recipient account has not registered a storage deposit for that token. Fee recipients are often relayer/operator accounts that may not pre-register for every bridged token, especially newly deployed ones. This is a realistic, unprivileged-user-reachable scenario: any user who initiates a transfer with a non-zero fee can trigger this path.

### Recommendation
Replace `.detach()` with a proper callback chain that checks the promise result and either retries or logs a recoverable failure event. At minimum, add a DAO-accessible `retry_fee_transfer` function that can re-dispatch stuck fee amounts. The pattern used for the main recipient transfer (a checked callback via `fin_transfer_send_tokens_callback`) should be applied consistently to fee transfers as well.

### Proof of Concept
1. A new bridge token `TOKEN_X` is deployed on NEAR via `deploy_token`.
2. A user on EVM calls `initTransfer` for `TOKEN_X` with `fee = 100`, `amount = 1000`, specifying a `feeRecipient` that has never registered storage for `TOKEN_X` on NEAR.
3. The MPC signs the transfer; a relayer calls `fin_transfer` on NEAR.
4. The bridge mints 900 `TOKEN_X` to the recipient — this succeeds.
5. `fin_transfer_send_tokens_callback` fires. It calls `mint(fee_recipient, 100)` with `.detach()`.
6. The `mint` call panics internally because `fee_recipient` has no storage deposit for `TOKEN_X`.
7. Because `.detach()` was used, the panic is silently discarded. No callback fires, no state is rolled back.
8. Result: 100 `TOKEN_X` is never minted. The fee recipient receives nothing. The 100-unit gap between locked EVM collateral (1000) and minted NEAR supply (900) is permanent and unrecoverable, corrupting bridge collateralization. [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1692-1747)
```rust
    pub fn fin_transfer_send_tokens_callback(
        &mut self,
        #[serializer(borsh)] transfer_message: TransferMessage,
        #[serializer(borsh)] fee_recipient: &AccountId,
        #[serializer(borsh)] is_ft_transfer_call: bool,
        #[serializer(borsh)] storage_owner: &AccountId,
        #[serializer(borsh)] lock_actions: Vec<LockAction>,
    ) {
        let token = self.get_token_id(&transfer_message.token);

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
        } else {
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

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
    }
```
