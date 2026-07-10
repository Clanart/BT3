### Title
Malicious NEAR Token Can Call `ft_on_transfer` Without Transferring Tokens, Minting Unbacked EVM Bridge Tokens — (File: near/omni-bridge/src/lib.rs)

### Summary

The NEAR omni-bridge's `ft_on_transfer` entry point trusts the calling token contract to have actually transferred tokens. Because token registration is fully permissionless (`log_metadata` → `deployToken` on EVM → `bind_token` on NEAR), an attacker can deploy a malicious NEAR token, register it through the normal bridge flow, and then have that token call `ft_on_transfer` with an arbitrary `amount` without transferring any real tokens. The bridge processes the transfer, emits `InitTransfer`, and a relayer finalizes it on EVM, minting unbacked bridge tokens to the attacker.

### Finding Description

**Step 1 — Permissionless token registration.**

`log_metadata` accepts any `token_id` with no validation:

```rust
pub fn log_metadata(&self, token_id: &AccountId) -> Promise {
    ext_token::ext(token_id.clone())
        .with_static_gas(LOG_METADATA_GAS)
        .ft_metadata()
        ...
}
``` [1](#0-0) 

The MPC signs whatever metadata the token returns. Anyone can then call `deployToken` on EVM with the resulting signature (also permissionless per SECURITY.md), and `bind_token` on NEAR with proof of the EVM `DeployToken` event. After `bind_token_callback`, the malicious NEAR token is fully registered: [2](#0-1) 

**Step 2 — `ft_on_transfer` trusts the caller unconditionally.**

`ft_on_transfer` identifies the token solely by `env::predecessor_account_id()` and passes the caller-supplied `amount` directly into `init_transfer`:

```rust
pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
    let token_id = env::predecessor_account_id();
    ...
    self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
}
``` [3](#0-2) 

There is no check that the token actually transferred `amount` tokens to the bridge before calling this callback. A malicious token can implement `ft_transfer_call` to invoke `ft_on_transfer` on the bridge with any `amount` without moving a single token.

**Step 3 — `init_transfer` locks the (fake) amount and emits `InitTransfer`.**

`init_transfer` calls `lock_tokens_if_needed`, which increments `locked_tokens[(Eth, malicious.near)]` by the fake amount, then emits `InitTransferEvent`: [4](#0-3) 

**Step 4 — `sign_transfer` succeeds because the token is registered.**

`sign_transfer` looks up the EVM address for `malicious.near` (set during `bind_token`), normalizes the amount, and requests an MPC signature. The trusted relayer calls this on behalf of any pending transfer without verifying the underlying token movement: [5](#0-4) 

**Step 5 — EVM `finTransfer` mints unbacked bridge tokens.**

The MPC-signed payload is submitted to EVM `finTransfer`, which verifies the signature and mints bridge tokens to the attacker's EVM address: [6](#0-5) 

### Impact Explanation

An attacker can mint an arbitrary quantity of EVM bridge tokens backed by zero locked NEAR tokens. The attacker can immediately sell these tokens on secondary markets, draining liquidity from legitimate bridge users. This directly breaks bridge collateralization and constitutes unauthorized minting of bridged assets — a Critical impact under the allowed scope.

### Likelihood Explanation

All steps are fully permissionless:
- Deploying a NEAR account with a malicious token contract requires only NEAR gas.
- `log_metadata`, `deployToken` (EVM), and `bind_token` (NEAR) are all open to any caller.
- The trusted relayer processes any valid `InitTransfer` event without inspecting the underlying token.

No privileged access, leaked keys, or colluding parties are required.

### Recommendation

1. **Validate token registration before processing `ft_on_transfer`.** In `ft_on_transfer`, check that `env::predecessor_account_id()` is a token registered in `token_address_to_id` (or `deployed_tokens`) before calling `init_transfer`. Reject calls from unregistered tokens.

2. **Alternatively, restrict `log_metadata` to tokens that pass a legitimacy check** (e.g., require the token to be an existing, non-malicious NEP-141 contract verified by governance or a whitelist), analogous to the recommended fix in the external report of verifying that a staking contract was deployed by the system using an approved template.

3. **For the EVM side**, `initTransfer` should similarly validate that `tokenAddress` is a registered bridge token or a token explicitly whitelisted by governance, rather than accepting any arbitrary ERC20. [7](#0-6) 

### Proof of Concept

```rust
// 1. Deploy malicious NEAR token at `malicious.near`
//    - ft_metadata() returns valid name/symbol/decimals
//    - ft_transfer_call() calls ft_on_transfer on bridge with amount=1_000_000
//      WITHOUT actually moving any tokens

// 2. Register the malicious token (all permissionless)
bridge.log_metadata("malicious.near");          // MPC signs metadata
evm_bridge.deployToken(mpc_sig, metadata);      // EVM bridge token deployed
bridge.bind_token(proof_of_evm_deploy_token);   // NEAR registers mapping

//

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-283)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
            }
            BridgeOnTransferMsg::FastFinTransfer(fast_fin_transfer_msg) => {
                self.fast_fin_transfer(token_id, amount, signer_id, fast_fin_transfer_msg)
            }
            BridgeOnTransferMsg::UtxoFinTransfer(utxo_fin_transfer_msg) => self.utxo_fin_transfer(
                token_id,
                amount,
                &signer_id,
                &sender_id,
                utxo_fin_transfer_msg,
            ),
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
        };

        promise_or_promise_index_or_value.as_return();
    }
```

**File:** near/omni-bridge/src/lib.rs (L317-327)
```rust
    pub fn log_metadata(&self, token_id: &AccountId) -> Promise {
        ext_token::ext(token_id.clone())
            .with_static_gas(LOG_METADATA_GAS)
            .ft_metadata()
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(LOG_METADATA_CALLBACK_GAS)
                    .with_attached_deposit(env::attached_deposit())
                    .log_metadata_callback(token_id),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L447-521)
```rust
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
        let transfer_message = self.get_transfer_message(transfer_id);

        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }

        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
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

        let message = DestinationChainMsg::from_json(&transfer_message.msg)
            .and_then(|s| s.destination_msg())
            .unwrap_or_default();

        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };

        let payload = near_sdk::env::keccak256_array(
            transfer_payload
                .encode_hashable()
                .near_expect(BridgeError::Borsh),
        );

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_TRANSFER_CALLBACK_GAS)
                    .sign_transfer_callback(transfer_payload, &transfer_message.fee),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1241-1301)
```rust
    #[private]
    pub fn bind_token_callback(
        &mut self,
        attached_deposit: NearToken,
        #[callback_result]
        #[serializer(borsh)]
        call_result: Result<ProverResult, PromiseError>,
    ) -> NearToken {
        let Ok(ProverResult::DeployToken(deploy_token)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        require!(
            self.factories
                .get(&deploy_token.emitter_address.get_chain())
                == Some(deploy_token.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let storage_usage = env::storage_usage();

        self.add_token(
            &deploy_token.token,
            &deploy_token.token_address,
            deploy_token.decimals,
            deploy_token.origin_decimals,
        );

        require!(
            self.locked_tokens
                .insert(
                    &(
                        deploy_token.token_address.get_chain(),
                        deploy_token.token.clone(),
                    ),
                    &0,
                )
                .is_none(),
            TokenLockError::TokenAlreadyLocked.as_ref()
        );

        let required_deposit = env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into());

        require!(
            attached_deposit >= required_deposit,
            BridgeError::InsufficientStorageDeposit.as_ref()
        );

        env::log_str(
            &OmniBridgeEvent::BindTokenEvent {
                token_id: deploy_token.token,
                token_address: deploy_token.token_address,
                decimals: deploy_token.decimals,
                origin_decimals: deploy_token.origin_decimals,
            }
            .to_log_string(),
        );

        attached_deposit.saturating_sub(required_deposit)
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

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

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```
