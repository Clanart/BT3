### Title
Permanent Fund Lock When EVM Token Transfer to Blacklisted or Paused Recipient Fails in `finTransfer` — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.sol::finTransfer`, the MPC signature covers the exact `recipient` address. If the token transfer to that recipient fails at execution time (e.g., USDC blacklisting, token pause, or ERC-20 revert on zero-balance), the transaction reverts atomically — but the signature remains permanently bound to the failing recipient. There is no protocol mechanism to redirect funds to an alternative address or to issue a new MPC signature for a different recipient. The NEAR-side tokens were already burned or locked when `init_transfer` was called, so the user's funds become permanently unclaimable.

---

### Finding Description

`OmniBridge.sol::finTransfer` follows this sequence:

1. Check `completedTransfers[payload.destinationNonce]` — revert if already used.
2. Set `completedTransfers[payload.destinationNonce] = true`.
3. Verify MPC ECDSA signature over a Borsh-encoded payload that **includes `payload.recipient`**.
4. Transfer tokens to `payload.recipient` (via `safeTransfer`, `mint`, ETH send, or ERC-1155 transfer). [1](#0-0) 

Because Ethereum transactions are atomic, if step 4 reverts, step 2 also reverts and the nonce is not permanently consumed. However, the signature is cryptographically bound to the specific `payload.recipient` (it is part of the Borsh-encoded message that is hashed and signed by the MPC). [2](#0-1) 

If the recipient is USDC-blacklisted, the token is paused, or any other condition causes `safeTransfer` to revert, **every future call to `finTransfer` with the same valid MPC signature will also revert**. The nonce is never consumed, but the transfer can never succeed either.

On the NEAR side, when the user called `ft_on_transfer` → `init_transfer`, the tokens were already burned (deployed token) or locked in the bridge contract. The transfer message is stored in `pending_transfers`. [3](#0-2) 

There is no user-callable refund function, no admin rescue path, and no mechanism for the MPC to re-sign the same transfer with a different recipient. The `OmniBridge.sol` contract exposes no function to override the recipient for a stuck transfer. [4](#0-3) 

The same structural issue exists in the StarkNet bridge: `_set_transfer_finalised` is called before the token transfer, and the signature covers `payload.recipient`. If the transfer fails, the nonce reverts but the transfer is permanently unfinalizeable. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

The NEAR-side tokens are burned or locked at `init_transfer` time. If the EVM `finTransfer` can never succeed (blacklisted recipient, paused token), those tokens are permanently unclaimable. The user loses the full bridged amount with no recovery path available in the protocol.

This matches the allowed impact: *"Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** USDC and USDT maintain on-chain blacklists; a recipient can be blacklisted after the NEAR-side `init_transfer` is submitted but before the EVM `finTransfer` is executed (the MPC signing and relaying introduces a window). Token pausing (e.g., Circle pausing USDC during an incident) is another realistic trigger. A malicious user could also deliberately get themselves blacklisted to grief a relayer or to create a denial-of-service on a specific transfer. The bridge explicitly supports USDC and other regulated tokens as bridgeable assets.

---

### Recommendation

Add a fallback recipient mechanism: if the primary token transfer to `payload.recipient` fails, allow the funds to be claimed by the recipient from a pending-claims mapping, or allow an admin/MPC-authorized redirect to an alternative address. Concretely:

1. Wrap the token transfer in a try/catch (or use a low-level call with success check without reverting).
2. On failure, store the amount in a `pendingClaims[recipient][token]` mapping.
3. Expose a `claimFailed(address token)` function so the recipient can pull funds when the blocking condition is resolved (e.g., after being removed from the USDC blacklist).

Alternatively, allow the MPC to sign a "redirect" payload that moves a stuck transfer to a new recipient, with the original recipient's consent encoded in the signature.

---

### Proof of Concept

1. Alice holds 10,000 USDC on NEAR and initiates a bridge transfer to EVM address `0xAlice` via `ft_on_transfer` → `init_transfer`. The NEAR bridge burns Alice's 10,000 USDC.
2. The MPC signs a `TransferMessagePayload` with `recipient = 0xAlice`, `tokenAddress = USDC_EVM`, `amount = 10000`.
3. Before the relayer submits `finTransfer`, Circle blacklists `0xAlice` on EVM USDC.
4. Relayer calls `finTransfer(signature, payload)`. The call reaches line 351: `IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount)`. OpenZeppelin's `safeTransfer` calls USDC's `transfer`, which reverts because `0xAlice` is blacklisted. The entire transaction reverts.
5. The nonce is not consumed (reverted), but the signature is permanently tied to `0xAlice`. Every retry produces the same revert.
6. Alice's 10,000 USDC is permanently locked in the NEAR bridge contract with no recovery path. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-355)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-598)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }

    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }

    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }

    receive() external payable {}

    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }

    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}

    uint256[49] private __gap;
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

**File:** starknet/src/omni_bridge.cairo (L247-263)
```text
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
