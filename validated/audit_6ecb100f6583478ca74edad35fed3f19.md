### Title
Permanent Freeze of User Funds via Blacklisted Recipient in `finTransfer` — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.finTransfer` directly transfers tokens to the `payload.recipient` address inline. When the bridged token has a blacklisting mechanism (e.g., USDC/USDT), a blacklisted recipient causes every finalization attempt to revert. Because the recipient is embedded in the MPC-signed payload and cannot be changed, and because the NEAR bridge has no user-facing cancel/refund path for pending transfers, the user's source-chain funds are permanently frozen.

### Finding Description

In `OmniBridge.finTransfer`, the destination nonce is marked used at line 287 **before** the token transfer is attempted:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287
// ...
IERC20(payload.tokenAddress).safeTransfer(             // line 351
    payload.recipient,
    payload.amount
);
``` [1](#0-0) 

If `safeTransfer` reverts (e.g., USDC blacklist), the entire EVM transaction reverts, rolling back the nonce mark. The nonce is therefore never consumed, so the relayer can retry — but every retry will also revert. The recipient address is part of the MPC-signed `TransferMessagePayload` (it is Borsh-encoded and hashed before ECDSA recovery at line 309–311), so no relayer can substitute a different recipient without an entirely new MPC signature. [2](#0-1) 

On the NEAR side, the user's tokens were locked when `ft_on_transfer` → `init_transfer` was called, storing the transfer in `pending_transfers`. There is no user-facing cancel or refund function; the only recovery path is a privileged DAO call to `transfer_token_as_dao`. [3](#0-2) 

The identical pattern exists in the StarkNet bridge: `_set_transfer_finalised` is called before the `IERC20.transfer`, and if the transfer returns `false` the `assert(success, 'ERR_TRANSFER_FAILED')` reverts the whole transaction, leaving the nonce unconsumed and the source-chain funds permanently locked. [4](#0-3) 

### Impact Explanation

**Critical / High — Permanent freezing of user funds.**

A user who bridges USDC (or any token with a blacklisting mechanism) from NEAR to EVM and whose EVM recipient address is subsequently blacklisted by the token issuer will have their NEAR-side tokens locked indefinitely. No on-chain mechanism allows the user to cancel the pending transfer or redirect it to a non-blacklisted address. The funds are irrecoverable without out-of-band DAO intervention.

### Likelihood Explanation

**Medium.** USDC and USDT are among the most commonly bridged assets and both implement address blacklists. Blacklisting events occur regularly (sanctions enforcement, exchange hacks, etc.). A user whose recipient address is blacklisted after initiating a bridge transfer — or who mistakenly specifies a blacklisted address — will trigger this freeze with no self-service remedy.

### Recommendation

Replace the direct inline transfer with a pull-payment (claims) pattern:

```solidity
// Instead of transferring immediately:
claimable[payload.tokenAddress][payload.recipient] += payload.amount;

// Add a separate claim function:
function claim(address tokenAddress) external {
    uint256 amount = claimable[tokenAddress][msg.sender];
    claimable[tokenAddress][msg.sender] = 0;
    IERC20(tokenAddress).safeTransfer(msg.sender, amount);
}
```

This decouples finalization (nonce consumption, proof verification) from token delivery, so a blacklisted recipient cannot prevent the nonce from being permanently settled and cannot freeze source-chain funds.

### Proof of Concept

1. Alice calls `ft_transfer_call` on NEAR, sending 1 000 USDC to the bridge with `recipient = Bob_EVM_address`. Tokens are locked; the transfer is stored in `pending_transfers`.
2. Before the relayer finalizes, Bob's EVM address is added to the USDC blacklist (e.g., OFAC sanction).
3. The relayer calls `OmniBridge.finTransfer` with the MPC-signed payload containing `recipient = Bob_EVM_address`.
4. `IERC20(usdc).safeTransfer(Bob_EVM_address, 1000e6)` reverts — USDC blacklist check fails.
5. The entire EVM transaction reverts; `completedTransfers[nonce]` is rolled back.
6. Every subsequent retry by any relayer produces the same revert.
7. Alice's 1 000 USDC remain locked in the NEAR bridge `pending_transfers` map with no user-accessible cancel path.
8. Recovery requires the NEAR DAO to call `transfer_token_as_dao`, a privileged operation with no guaranteed timeline. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
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
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** near/omni-bridge/src/lib.rs (L523-619)
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
            )
        } else {
            let promise_index = env::promise_yield_create(
                "init_transfer_resume",
                json!({
                    "transfer_message": transfer_message,
                    "message_storage_account_id": message_storage_account_id,
                    "storage_owner": signer_id,
                })
                .to_string()
                .as_bytes(),
                INIT_TRANSFER_RESUME_GAS,
                GasWeight(0),
                PROMISE_REGISTER_ID,
            );

            let yield_id: CryptoHash = env::read_register(PROMISE_REGISTER_ID)
                .near_expect(BridgeError::ReadPromiseRegister)
                .try_into()
                .near_expect(BridgeError::ReadPromiseYieldId);

            let required_storage_balance = self.add_promise(&message_storage_account_id, &yield_id);

            self.update_storage_balance(
                env::current_account_id(),
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            env::log_str(&format!(
                "Yield init transfer until storage is available at {message_storage_account_id}"
            ));

            PromiseOrPromiseIndexOrValue::PromiseIndex(promise_index)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1511-1530)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn transfer_token_as_dao(
        &mut self,
        token: AccountId,
        amount: U128,
        recipient: AccountId,
        msg: Option<String>,
    ) -> Promise {
        if let Some(msg) = msg {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_CALL_GAS)
                .ft_transfer_call(recipient, amount, None, msg)
        } else {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        }
    }
```

**File:** starknet/src/omni_bridge.cairo (L242-263)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```
