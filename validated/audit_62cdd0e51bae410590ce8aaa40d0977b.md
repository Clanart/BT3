### Title
Reverting ETH Recipient in `finTransfer` Permanently Locks Bridged User Funds - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer()`, when bridging native ETH (`payload.tokenAddress == address(0)`), the contract pushes ETH directly to `payload.recipient` via a low-level call and reverts the entire transaction if the push fails. Because the user's NEAR-side tokens are already burned/locked at `init_transfer` time with no cross-chain refund path, any transfer whose EVM recipient is a contract that reverts on ETH receipt results in a permanently irrecoverable fund lock.

---

### Finding Description

In `finTransfer`, the nonce is marked consumed and then ETH is pushed to the recipient:

```solidity
completedTransfers[payload.destinationNonce] = true;   // L287 – marked before transfer
// ... signature verification ...
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();            // L322 – hard revert on failure
}
``` [1](#0-0) 

Because Solidity reverts roll back all state changes, the `completedTransfers` mark is also rolled back, so the nonce is technically reusable. However, if `payload.recipient` is a contract whose `receive`/`fallback` always reverts (e.g., a contract wallet with no ETH acceptance, a self-destructed contract, or a contract upgraded after the transfer was signed), every retry of `finTransfer` will also revert. The transfer can never be finalized.

The NEAR-side tokens were already burned or locked at `init_transfer` time:

```rust
// tokens burned/locked in init_transfer, stored in transfer_messages
self.current_origin_nonce += 1;
// ...
self.init_transfer_internal(transfer_message, signer_id)
``` [2](#0-1) 

There is no cross-chain callback, no `cancel_transfer`, and no timeout-based refund path in the NEAR bridge for transfers destined for other chains. The `fin_transfer_send_tokens_callback` refund path only applies to NEAR-to-NEAR token transfers, not NEAR-to-EVM flows. [3](#0-2) 

---

### Impact Explanation

**Critical – Permanent freezing of user funds.**

The user's NEAR tokens are burned/locked at `init_transfer`. The MPC signs the payload (including the fixed `recipient` address). If the EVM recipient contract reverts on ETH receipt, `finTransfer` can never succeed. There is no on-chain recovery path: the nonce is never permanently consumed (so no replay protection is broken), but the NEAR-side funds are irrecoverably gone. The ETH that the relayer must supply with each `finTransfer` call is returned to the relayer on revert, but the user's bridged value is permanently stranded.

---

### Likelihood Explanation

**Medium.** Realistic triggering scenarios include:

1. **Contract wallet upgrade**: A user specifies a smart contract wallet as recipient. After the MPC signs the transfer (which can take time), the wallet is upgraded or its `receive` function is removed. All subsequent `finTransfer` attempts revert.
2. **Self-destructed contract**: The recipient contract is self-destructed between `init_transfer` and `finTransfer`. Post-EIP-6780, a self-destructed contract address has no code and no `receive`, so ETH pushes to it may fail depending on implementation.
3. **Conditional revert**: A contract that conditionally accepts ETH (e.g., based on a paused state or access control) is paused after the transfer is signed.

The user controls the recipient address at `init_transfer` time, and the MPC signs it immutably. No privileged role is required to trigger this — any bridge user who specifies a contract recipient is exposed.

---

### Recommendation

Replace the push pattern with a pull pattern for native ETH finalization:

1. Instead of `payload.recipient.call{value: payload.amount}("")`, store the claimable amount in a mapping: `ethBalance[payload.recipient] += payload.amount`.
2. Add a `withdrawETH()` function that lets recipients pull their ETH: `payable(msg.sender).call{value: ethBalance[msg.sender]}("")`.
3. Mark the nonce as completed regardless of whether the recipient can accept ETH, so the bridge state is always consistent.
4. Alternatively, emit an event and allow the relayer to specify a fallback recipient (e.g., the relayer itself) if the primary recipient reverts, with the user able to claim from the relayer off-chain.

---

### Proof of Concept

1. User calls `init_transfer` on NEAR with `recipient = address(RevertingWallet)` (a contract whose `receive()` does `revert()`). NEAR tokens are burned.
2. MPC signs `TransferMessagePayload` with `tokenAddress = address(0)`, `recipient = address(RevertingWallet)`, `amount = X`.
3. Relayer calls `finTransfer(signatureData, payload)` on EVM, attaching `X` ETH.
4. `completedTransfers[nonce] = true` is set.
5. `RevertingWallet.receive()` reverts → `FailedToSendEther` is thrown → entire transaction reverts → `completedTransfers[nonce]` is rolled back.
6. Relayer retries indefinitely; every attempt reverts.
7. User's NEAR tokens are permanently burned. ETH is never delivered. No recovery function exists. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
```

**File:** near/omni-bridge/src/lib.rs (L523-583)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );

        let required_storage_balance =
            self.required_balance_for_init_transfer_message(transfer_message.clone());

        let message_storage_account_id = transfer_message
            .calculate_storage_account_id(init_transfer_msg.external_id.map(String::from));

        // Choose storage payer or whether to yield execution until storage is available
        if self
            .try_to_transfer_balance_from_message_account(
                &message_storage_account_id,
                NearToken::from_yoctonear(init_transfer_msg.native_token_fee.0),
                &signer_id,
                required_storage_balance,
            )
            .is_ok()
            || (self.has_storage_balance(
                &signer_id,
                required_storage_balance.saturating_add(NearToken::from_yoctonear(
                    init_transfer_msg.native_token_fee.0,
                )),
            ) && (init_transfer_msg.native_token_fee.0 == 0
                || !self.acl_has_role(Role::NativeFeeRestricted.into(), signer_id.clone())))
        {
            PromiseOrPromiseIndexOrValue::Value(
                self.init_transfer_internal(transfer_message, signer_id),
```

**File:** near/omni-bridge/src/lib.rs (L1692-1718)
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
```
