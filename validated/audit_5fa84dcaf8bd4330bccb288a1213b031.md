### Title
Push-Only Token Delivery in `finTransfer` Permanently Locks Source-Chain Assets When Recipient Rejects Transfer — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.finTransfer` uses a push pattern to deliver tokens to the recipient. If the recipient address is a contract that always reverts on token receipt (no ETH `receive`/`fallback`, no `IERC1155Receiver`, or a blacklisted ERC-20 recipient), every `finTransfer` call reverts. Because no cancel/refund path exists on the NEAR source chain, the tokens burned or locked there are permanently irrecoverable by the user.

---

### Finding Description

`finTransfer` in `OmniBridge.sol` marks the destination nonce as consumed **before** attempting the token push:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287
```

It then pushes tokens to the recipient via one of four branches:

```solidity
// Native ETH — low-level call, reverts on failure
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();                // lines 319-322

// ERC-1155 — calls onERC1155Received on recipient, can revert
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this), payload.recipient, ...);              // lines 324-330

// Bridge token — mint to recipient
IBridgeToken(payload.tokenAddress).mint(
    payload.recipient, payload.amount);                  // lines 339-349

// Native ERC-20 — safeTransfer, reverts on failure
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient, payload.amount);                  // lines 351-354
```

Because Solidity reverts the entire transaction on failure, the nonce marking at line 287 is also rolled back, so the nonce is **not** consumed. The transfer can be retried — but if the recipient contract **always** rejects the token type, `finTransfer` can never succeed.

On the NEAR source chain, once `init_transfer_internal` succeeds, the tokens are either burned (bridge tokens) or locked (native tokens) and the `pending_transfers` entry is stored. There is **no user-callable cancel function** anywhere in `near/omni-bridge/src/lib.rs` that would allow the sender to reclaim those tokens. `remove_transfer_message` is only called internally from `claim_fee_callback` and `sign_transfer_callback`, neither of which is reachable by the original sender in this failure scenario. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

- For bridge tokens (e.g., wNEAR bridged to EVM): `burn_tokens_if_needed` is called during `init_transfer_internal`, destroying the tokens on NEAR. They cannot be re-minted without a successful `finTransfer` on EVM, which can never complete.
- For native tokens (e.g., USDC originating from EVM): `lock_tokens_if_needed` increments the `locked_tokens` counter on NEAR. The tokens remain in the bridge but are permanently inaccessible to the user because no cancel path exists.

In both cases the `pending_transfers` entry persists on NEAR, the MPC-signed payload is fixed (recipient cannot be changed without a new MPC signature), and the user has no on-chain recourse. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The most realistic trigger is the native-ETH branch: a user bridges tokens from NEAR to an EVM contract address that has no `receive` or `fallback` function (e.g., a multisig, a DAO treasury, a DeFi protocol contract). This is a common mistake. The ERC-1155 branch is also realistic because `safeTransferFrom` calls `onERC1155Received` on the recipient; any contract that does not implement `IERC1155Receiver` will cause a permanent lock. ERC-20 blacklists (USDC, USDT) provide a third realistic path. [6](#0-5) 

---

### Recommendation

1. **Pull pattern on EVM:** Instead of pushing tokens directly to `payload.recipient` inside `finTransfer`, credit a `claimable[recipient][token] += amount` mapping and emit an event. Provide a separate `claimTokens()` function the recipient calls to pull their funds. This mirrors the fix described in the referenced Moloch report.

2. **Cancel/refund path on NEAR:** Add a user-callable `cancel_transfer(transfer_id)` function on the NEAR bridge that, after a timeout or explicit MPC-signed cancellation proof, burns the pending-transfer entry and re-mints/unlocks the original tokens back to the sender.

3. **Recipient validation:** For the native-ETH case, consider requiring the recipient to be an EOA (check `payload.recipient.code.length == 0`) or accept an explicit opt-in flag.

---

### Proof of Concept

1. Alice holds 1 ETH worth of wNEAR on NEAR and calls `ft_transfer_call` on the NEAR bridge, specifying `recipient = 0xDeadBeef...` (a deployed contract with no `receive` function) on EVM.
2. `init_transfer_internal` burns Alice's bridge tokens and stores the `TransferMessage` in `pending_transfers`.
3. A relayer calls `finTransfer` on EVM with the MPC-signed payload. Execution reaches line 319: `payload.recipient.call{value: payload.amount}("")` returns `success = false`. The function reverts with `FailedToSendEther`. The nonce is rolled back.
4. Every subsequent `finTransfer` attempt reverts identically.
5. Alice has no `cancel_transfer` function to call on NEAR. Her tokens are burned and the `pending_transfers` entry is orphaned forever. [7](#0-6) [2](#0-1)

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

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L96-107)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }
```
