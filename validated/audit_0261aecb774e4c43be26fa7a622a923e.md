### Title
Zero-Address Recipient Not Validated in `finTransfer`, Enabling Permanent Burn of Bridged Native ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol`'s `finTransfer` sends native ETH directly to `payload.recipient` without checking whether it equals `address(0)`. Because the NEAR-side `init_transfer` also performs no zero-address check on the recipient, a user can initiate a cross-chain transfer specifying `eth:0x0000000000000000000000000000000000000000` as the EVM recipient. The bridge signs and finalizes the transfer, and the ETH is permanently burned.

---

### Finding Description

**Root cause — EVM side (`OmniBridge.sol`, `finTransfer`):**

When `payload.tokenAddress == address(0)` (the sentinel for native ETH), the contract unconditionally forwards the ETH to `payload.recipient`:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
```

There is no guard of the form `if (payload.recipient == address(0)) revert ...`. A low-level `.call` to `address(0)` with ETH value succeeds (the zero address has no code; the EVM returns `(true, "")`) and the ETH is irrecoverably burned. [1](#0-0) 

**Root cause — NEAR side (`near/omni-bridge/src/lib.rs`, `init_transfer`):**

The only recipient validation performed is a chain-kind check:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
```

`OmniAddress::is_zero()` exists in `near/omni-types/src/lib.rs` and correctly identifies zero addresses for every supported chain, but it is never called in the transfer-initiation path. [2](#0-1) [3](#0-2) 

No `ZeroAddress`, `InvalidRecipient`, or equivalent error is defined or checked anywhere in the EVM contracts for `payload.recipient`. [4](#0-3) 

---

### Impact Explanation

A user who specifies `eth:0x0000000000000000000000000000000000000000` as the EVM recipient for a native-ETH bridge transfer will have their ETH permanently burned at `finTransfer` time. The nonce is marked used (`completedTransfers[payload.destinationNonce] = true`) before the send, so the transfer cannot be replayed or recovered. The ETH is irrecoverably lost from the bridge's perspective.

This is a **High** impact: it misdirects and permanently destroys bridged value, breaking the bridge's collateralization guarantee for that transfer. [5](#0-4) 

---

### Likelihood Explanation

Any unprivileged user who calls `ft_transfer_call` on the NEAR bridge (or `initTransfer` on any supported source chain) can supply `eth:0x0000000000000000000000000000000000000000` as the recipient. No privileged role is required. The NEAR bridge accepts the message, the MPC relayer signs it, and `finTransfer` on EVM executes without reversion. The scenario is reachable by accident (copy-paste error, uninitialized address) or by a user deliberately burning their own funds. [6](#0-5) 

---

### Recommendation

1. **EVM (`OmniBridge.sol`, `finTransfer`):** Add an explicit guard before any asset dispatch:
   ```solidity
   if (payload.recipient == address(0)) revert InvalidRecipient();
   ```
   This should cover all branches (native ETH, ERC-20, ERC-1155, bridge-token mint). [7](#0-6) 

2. **NEAR (`near/omni-bridge/src/lib.rs`, `init_transfer`):** Reject zero-address recipients at the source using the already-available `OmniAddress::is_zero()`:
   ```rust
   require!(
       !init_transfer_msg.recipient.is_zero(),
       BridgeError::InvalidRecipient.as_ref()
   );
   ``` [2](#0-1) [3](#0-2) 

---

### Proof of Concept

1. On NEAR, call `ft_transfer_call` on the wrapped-ETH token contract with:
   ```json
   {
     "receiver_id": "<omni-bridge-account>",
     "amount": "1000000000000000000",
     "msg": "{\"InitTransfer\":{\"recipient\":\"eth:0x0000000000000000000000000000000000000000\",\"fee\":\"0\",\"native_token_fee\":\"0\"}}"
   }
   ```
2. NEAR `init_transfer` accepts the message (only chain-kind check passes). [2](#0-1) 
3. The trusted relayer calls `sign_transfer`; the MPC signer produces a valid ECDSA signature over the payload containing `recipient = address(0)`. [8](#0-7) 
4. Anyone calls `finTransfer` on the EVM `OmniBridge` with the signed payload (`tokenAddress = address(0)`, `recipient = address(0)`, `amount = 1e18`).
5. The contract marks the nonce used, then executes:
   ```solidity
   (bool success, ) = address(0).call{value: 1e18}("");
   // success == true, ETH is burned
   ```
   The ETH is permanently destroyed. [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L523-557)
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
```

**File:** near/omni-types/src/lib.rs (L299-312)
```rust
    pub fn is_zero(&self) -> bool {
        match self {
            Self::Eth(address)
            | Self::Arb(address)
            | Self::Base(address)
            | Self::Bnb(address)
            | Self::Pol(address)
            | Self::HyperEvm(address)
            | Self::Abs(address) => address.is_zero(),
            Self::Near(address) => *address == ZERO_ACCOUNT_ID,
            Self::Sol(address) | Self::Fogo(address) => address.is_zero(),
            Self::Btc(address) | Self::Zcash(address) => address.is_empty(),
            Self::Strk(address) => address.is_zero(),
        }
```
