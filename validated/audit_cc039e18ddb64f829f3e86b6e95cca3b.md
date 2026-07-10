### Title
Blacklisted ERC-20 Recipient Permanently Freezes Bridged Funds — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`finTransfer` in `OmniBridge.sol` directly calls `safeTransfer` to `payload.recipient` with no fallback. If the recipient is blacklisted for the token (e.g., USDC or USDT), every finalization attempt reverts. Because the recipient address is hardcoded in the MPC-signed payload and there is no user-accessible cancel or redirect mechanism on NEAR, the source-chain funds are permanently frozen.

---

### Finding Description

In `finTransfer`, the nonce is first marked used at line 287, then the token transfer is attempted. For a plain ERC-20 (the `else` branch), the call is:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [1](#0-0) [2](#0-1) 

Because `safeTransfer` reverts on failure, the entire transaction reverts — including the nonce marking at line 287. The nonce is therefore never consumed, but the call can never succeed either, because the recipient remains blacklisted.

On the NEAR side, when a user calls `ft_transfer_call` to initiate a transfer to EVM, `init_transfer_internal` immediately burns (for deployed tokens) or locks (for native tokens) the user's funds and stores the transfer message in `pending_transfers`: [3](#0-2) 

The MPC then signs a payload that embeds `payload.recipient`. There is no protocol-level mechanism for the user to change the recipient or cancel the transfer and recover funds. The only recovery path is `transfer_token_as_dao`, which requires DAO role: [4](#0-3) 

There is no user-callable cancel or escrow fallback anywhere in the EVM contract or the NEAR contract.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

- For native EVM tokens (e.g., USDC locked in the EVM bridge when originally bridged to NEAR): when the user bridges back to EVM, the deployed NEAR token is burned. If `finTransfer` on EVM always reverts, the USDC is permanently locked in the EVM bridge with no release path.
- For native NEAR tokens bridged to EVM: tokens are locked on NEAR and can never be unlocked because `finTransfer` never succeeds.

In both cases, the user loses their funds with no protocol-level recovery.

---

### Likelihood Explanation

**High.** USDC (Circle) and USDT (Tether) are among the most commonly bridged tokens and both maintain active blacklists. A user's address can be blacklisted:
- After the `initTransfer` is submitted but before `finTransfer` is called (race condition).
- Due to regulatory action against the recipient address.
- Deliberately, by an adversary who controls the recipient address and gets it blacklisted to grief the sender.

No special privilege is required to trigger this — any ordinary bridge user bridging USDC/USDT to a blacklisted EVM address is affected.

---

### Recommendation

Wrap the `safeTransfer` call in a try/catch (or use a low-level call with a success check) and, on failure, deposit the funds into an escrow mapping keyed by `(destinationNonce, recipient)`. Add a separate `claimEscrow` function that lets the recipient (or an admin-designated alternative address) withdraw the escrowed funds later. This mirrors the fix applied in the referenced Wagmi Leverage report.

```solidity
// Pseudocode
try IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount) {
    // success
} catch {
    escrow[payload.destinationNonce] = EscrowEntry({
        token: payload.tokenAddress,
        recipient: payload.recipient,
        amount: payload.amount
    });
    emit TransferEscrowed(...);
}
```

---

### Proof of Concept

1. Alice holds 10,000 USDC on NEAR (bridged from Ethereum; the USDC is locked in the EVM `OmniBridge`).
2. Alice calls `ft_transfer_call` on the NEAR bridge to bridge 10,000 USDC back to her EVM address `0xAlice`. The NEAR deployed token is burned; the transfer message is stored in `pending_transfers`.
3. Circle blacklists `0xAlice` (e.g., due to a regulatory freeze).
4. A relayer calls `finTransfer` on the EVM `OmniBridge` with the MPC-signed payload specifying `recipient = 0xAlice`.
5. `safeTransfer(0xAlice, 10000e6)` reverts because `0xAlice` is blacklisted.
6. The entire transaction reverts; `completedTransfers[nonce]` is rolled back to `false`.
7. Every subsequent `finTransfer` attempt reverts for the same reason.
8. The 10,000 USDC remains locked in the EVM `OmniBridge` forever. Alice has no user-callable function to redirect the transfer or recover her funds on NEAR. [5](#0-4) [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L1511-1529)
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
