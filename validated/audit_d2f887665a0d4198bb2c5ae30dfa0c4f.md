### Title
Unverified Fire-and-Forget Burn Allows Unbacked Cross-Chain Token Minting - (File: near/omni-bridge/src/lib.rs)

### Summary
`burn_tokens_if_needed` fires the cross-contract `burn` call with `.detach()`, discarding the promise result entirely. If the burn fails for any reason, the bridge has already committed its state (transfer recorded, nonce incremented, `InitTransferEvent` emitted), allowing the destination chain to release or mint tokens while the user's NEAR-side bridged tokens remain unburned.

### Finding Description
`burn_tokens_if_needed` is the sole mechanism for destroying deployed (bridged) NEAR tokens when a user bridges them back to their origin chain:

```rust
// near/omni-bridge/src/lib.rs:1806-1813
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)   // only 3 TGas
            .burn(amount)
            .detach();                          // result never checked
    }
}
```

`BURN_TOKEN_GAS` is set to `Gas::from_tgas(3)` — a very tight budget. The `.detach()` call schedules the cross-contract promise and immediately discards any future result, whether success or failure.

This function is called in three critical paths, all of which have already mutated bridge state before the burn fires:

1. **`init_transfer_internal` (line 1851)** — called from `ft_on_transfer`. By the time `burn_tokens_if_needed` is reached, `add_transfer_message` has already inserted the pending transfer and `try_update_storage_balance` has already succeeded. The function then returns `U128(0)` (no refund), finalizing the `ft_transfer_call`. The `InitTransferEvent` is emitted immediately after.

2. **`fast_fin_transfer_to_other_chain` (line 932)** — the fast transfer entry is added and a new `TransferMessage` is created before the burn fires.

3. **`resolve_fast_transfer` (line 904)** — the burn fires before `remove_fast_transfer`, but the token transfer to the recipient has already been dispatched in the preceding promise chain.

The `burn` function in `OmniToken` calls `internal_withdraw` on `env::predecessor_account_id()` (the bridge contract). If the token contract is paused, has been upgraded with additional logic exceeding 3 TGas, or panics for any reason, the burn silently fails. The bridge has no callback to detect this and no rollback path.

### Impact Explanation
When the burn fails silently:
- The bridge's `pending_transfers` map contains a valid, finalized transfer entry.
- The `InitTransferEvent` log is emitted and observable by relayers.
- A relayer submits the proof to the destination chain (EVM, Solana, StarkNet).
- The destination chain releases or mints the full token amount to the user.
- The user's NEAR-side bridged tokens are **not** burned — they remain in the bridge contract's balance in the token contract, or can be reclaimed.

This breaks bridge collateralization: the destination chain has released assets that are no longer backed by a corresponding burn on NEAR, creating unbacked supply.

### Likelihood Explanation
The 3 TGas budget is the primary realistic trigger. NEAR's NEP-141 `internal_withdraw` itself is cheap, but any token contract that has been upgraded to include event emission, hooks, or additional validation beyond the minimal `OmniToken` implementation will exceed this budget. Additionally, if the token contract is paused at the moment the burn fires (a race condition between a DAO pause action and a user transfer), the burn panics and `.detach()` silently swallows it. An attacker who controls a deployed token contract (e.g., a token deployer who later upgrades the contract) can deliberately cause the burn to fail.

### Recommendation
Replace `.detach()` with a proper callback that verifies the burn succeeded. If the burn fails, the callback must revert the transfer state (remove the pending transfer entry and refund the user). Example pattern:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) -> Option<Promise> {
    if self.is_deployed_token(&token) {
        Some(
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .then(Self::ext(env::current_account_id())
                    .with_static_gas(BURN_CALLBACK_GAS)
                    .on_burn_complete(...))
        )
    } else {
        None
    }
}
```

Also increase `BURN_TOKEN_GAS` from 3 TGas to at least 10 TGas to accommodate realistic token contract implementations.

### Proof of Concept

**Setup:** Alice holds 1000 units of a bridged NEAR token (`weth.bridge.near`) that was originally minted when she bridged WETH from Ethereum. She wants to bridge it back.

1. Alice calls `ft_transfer_call(bridge.near, 1000, init_transfer_msg)` on `weth.bridge.near`.
2. `weth.bridge.near` transfers 1000 tokens to `bridge.near`'s balance and calls `ft_on_transfer` on `bridge.near`.
3. `bridge.near::ft_on_transfer` → `init_transfer_internal`:
   - `add_transfer_message` inserts the pending transfer. [1](#0-0) 
   - `try_update_storage_balance` succeeds. [2](#0-1) 
   - `burn_tokens_if_needed` fires `weth.bridge.near::burn(1000)` with `.detach()` and only 3 TGas. [3](#0-2) 
   - `InitTransferEvent` is emitted. [4](#0-3) 
   - Returns `U128(0)` — no refund to Alice.
4. The burn call to `weth.bridge.near` fails (e.g., gas exhaustion at 3 TGas, or contract paused). `.detach()` discards the failure. [5](#0-4) 
5. A relayer observes the `InitTransferEvent` and submits proof to the Ethereum OmniBridge.
6. Ethereum `finTransfer` verifies the MPC signature and releases 1000 WETH to Alice. [6](#0-5) 
7. Alice now holds 1000 WETH on Ethereum **and** 1000 `weth.bridge.near` tokens on NEAR (still in `bridge.near`'s balance, recoverable or re-bridgeable).

The bridge has released 1000 WETH that are no longer backed by a burned NEAR-side token, breaking collateralization.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1806-1813)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1834-1836)
```rust
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));
```

**File:** near/omni-bridge/src/lib.rs (L1838-1848)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L1863-1864)
```rust
        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-355)
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
```
