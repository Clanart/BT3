### Title
Blocklisted or Paused Token Recipient in EVM `finTransfer` Permanently Locks Bridged Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` delivers tokens to a recipient address that is fixed inside the MPC-signed payload. If the recipient's EVM address is blocklisted by the token contract (e.g., USDC, USDT) or the token is paused after the source-chain transfer is initiated, every call to `finTransfer` will revert. Because there is no mechanism to redirect the delivery to a different address, the user's funds are permanently irrecoverable: already burned or locked on the source chain, and permanently undeliverable on EVM.

---

### Finding Description

`OmniBridge.finTransfer` in `evm/src/omni-bridge/contracts/OmniBridge.sol` performs the following sequence atomically:

1. Marks the destination nonce as used:
   ```solidity
   completedTransfers[payload.destinationNonce] = true;   // line 287
   ```
2. Transfers tokens to `payload.recipient` via one of several paths (lines 317–355):
   - Native ETH: `payload.recipient.call{value: payload.amount}("")`
   - ERC-1155: `IERC1155.safeTransferFrom(..., payload.recipient, ...)`
   - Custom minter: `ICustomMinter.mint(payload.tokenAddress, payload.recipient, ...)`
   - Bridge token: `IBridgeToken.mint(payload.recipient, ...)`
   - Native ERC-20: `IERC20.safeTransfer(payload.recipient, ...)`

Because Solidity reverts atomically, if the token transfer at step 2 fails, the nonce marking at step 1 is also rolled back. The nonce is therefore never consumed, and the relayer can retry. However, the `payload.recipient` is **hardcoded in the MPC-signed message**. The bridge has no function to re-sign the payload with a different recipient, and no admin rescue path exists to redirect the delivery. If the recipient is blocklisted by the token or the token is globally paused, every retry will revert indefinitely.

On the source chain (NEAR), the tokens were already burned (`burn_tokens_if_needed`) or locked (`lock_tokens_if_needed`) at `init_transfer` time. There is no cross-chain refund path triggered by repeated EVM-side reverts. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

- Tokens are burned or locked on the source chain at `init_transfer` time and cannot be recovered from that side.
- `finTransfer` on EVM will revert on every attempt because the token contract rejects transfers to the blocklisted address.
- No admin function in `OmniBridge` can redirect the delivery or rescue the locked value.
- The user permanently loses the bridged amount.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium-High.**

- USDC and USDT — both tokens with active blocklist functionality — are natural candidates for bridging via Omni Bridge.
- USDC's Centre blocklist has been used against sanctioned addresses (OFAC compliance). A user whose EVM address is later sanctioned after initiating a bridge transfer would trigger this scenario.
- Token-level pause (e.g., USDT's `pause()`) can affect all recipients simultaneously, temporarily blocking all in-flight transfers for that token.
- The scenario requires no privileged access by the attacker; it is triggered by the token issuer's standard compliance tooling acting on the recipient's address.

---

### Recommendation

1. **Two-step delivery with a claimable escrow**: Instead of pushing tokens directly to `payload.recipient` inside `finTransfer`, hold them in a per-recipient claimable balance. The recipient (or an authorized substitute) can then pull them in a separate transaction. This decouples finalization from delivery and eliminates the permanent-lock scenario.

2. **Recipient override / rescue path**: Add an admin or DAO-controlled function that, given a valid signed payload whose delivery has failed N times, can redirect the tokens to an alternative address (e.g., the original sender's EVM address or a recovery vault), after which the source-chain refund flow can be triggered.

3. **Try/catch delivery**: Wrap the token transfer in a `try/catch` block. On failure, store the amount in a claimable mapping rather than reverting the entire transaction, so the nonce is consumed and the source-chain proof is not replayable.

---

### Proof of Concept

1. Alice holds USDC on NEAR and calls `init_transfer` to bridge 10,000 USDC to her EVM address `0xAlice`. NEAR burns/locks her tokens.
2. The MPC signs a `TransferMessagePayload` with `recipient = 0xAlice` and `tokenAddress = USDC_EVM`.
3. Centre (USDC issuer) blocklists `0xAlice` for compliance reasons.
4. Relayer calls `OmniBridge.finTransfer(sig, payload)`.
5. Execution reaches line 351: `IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount)` → USDC reverts because `0xAlice` is blocklisted.
6. The entire transaction reverts; `completedTransfers[nonce]` is rolled back.
7. Relayer retries — same revert every time.
8. Alice's 10,000 USDC are burned on NEAR and permanently undeliverable on EVM. No recovery path exists. [3](#0-2)

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

**File:** near/omni-bridge/src/lib.rs (L1850-1865)
```rust
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
