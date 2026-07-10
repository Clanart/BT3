### Title
Removed Custom Token Can Still Be Used in `initTransfer`, Causing Accounting Corruption or Permanent Fund Lock — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.sol` exposes `removeCustomToken` to remove a custom bridge token's registration. However, `initTransfer` contains no check that the token is currently registered. After removal, any user can still call `initTransfer` with the removed token. Because `isBridgeToken` and `customMinters` are now cleared, the token falls to the `else` branch and is **transferred to the bridge contract (locked) instead of burned**. If the NEAR bridge still holds the token's mapping, it will release/mint tokens on the NEAR side against a supply that was never burned on EVM — breaking collateralization. If the NEAR bridge mapping was also removed, the user's tokens are permanently locked with no recovery path.

### Finding Description

`removeCustomToken` clears all EVM-side registration state for a custom token:

```solidity
function removeCustomToken(address tokenAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
    delete isBridgeToken[tokenAddress];
    delete nearToEthToken[ethToNearToken[tokenAddress]];
    delete ethToNearToken[tokenAddress];
    delete customMinters[tokenAddress];
}
``` [1](#0-0) 

`initTransfer` has no corresponding guard. It dispatches on the current state of `customMinters` and `isBridgeToken`, both of which are now zero/false after removal:

```solidity
if (customMinters[tokenAddress] != address(0)) {
    // burn via custom minter — SKIPPED after removal
} else if (isBridgeToken[tokenAddress]) {
    // burn bridge token — SKIPPED after removal
} else {
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    // token is LOCKED, not burned
}
``` [2](#0-1) 

The function then unconditionally emits `InitTransfer`: [3](#0-2) 

The NEAR bridge's `fin_transfer_callback` processes this event. If the NEAR-side token mapping still exists (a common partial-removal scenario), it proceeds to release tokens: [4](#0-3) 

The NEAR bridge checks `token_decimals` for the token address. If the mapping is present, it denormalizes the amount and calls `send_tokens`, minting or releasing tokens on NEAR: [5](#0-4) 

### Impact Explanation

**Scenario A — NEAR mapping still present (accounting corruption):** The EVM bridge locks tokens that should have been burned. The NEAR bridge releases tokens against this event. The EVM bridge now holds a surplus of the removed token while NEAR supply grows unbacked. This directly breaks bridge collateralization — a High impact per the allowed scope ("Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value").

**Scenario B — NEAR mapping also removed (permanent lock):** The NEAR bridge panics at `TokenDecimalsNotFound` and rejects the transfer. The user's tokens are locked in the EVM bridge contract with no refund or cancellation mechanism. This is a Critical impact ("Permanent freezing, irrecoverable lock of user funds in bridge flows").

### Likelihood Explanation

The trigger requires an admin to call `removeCustomToken`. However, the subsequent loss is caused by an ordinary user calling `initTransfer` — a standard, public, unprivileged bridge action. Users who are unaware of the removal (e.g., acting on stale UI state or cached approvals) will trigger the bug. Partial removal (EVM only, NEAR not updated) is a realistic operational mistake, making Scenario A the more likely path.

### Recommendation

Add a registration check at the top of `initTransfer` (and `initTransfer1155`) to reject transfers for tokens that are not currently registered:

```solidity
require(
    tokenAddress == address(0) ||
    isBridgeToken[tokenAddress] ||
    customMinters[tokenAddress] != address(0) ||
    bytes(ethToNearToken[tokenAddress]).length > 0,
    "ERR_TOKEN_NOT_REGISTERED"
);
```

This mirrors the pattern already used in `deployToken`: [6](#0-5) 

### Proof of Concept

1. Admin calls `addCustomToken("token.near", tokenAddr, minterAddr, 18)` — token is registered, `isBridgeToken[tokenAddr] = true`, `customMinters[tokenAddr] = minterAddr`.
2. Admin calls `removeCustomToken(tokenAddr)` — all EVM-side state is cleared. NEAR bridge state is **not** updated.
3. User calls `initTransfer(tokenAddr, 1000, 0, 0, "alice.near", "")`.
4. `customMinters[tokenAddr] == address(0)` → skip. `isBridgeToken[tokenAddr] == false` → skip. `else` branch executes: `safeTransferFrom(user, bridge, 1000)` — 1000 tokens locked in bridge.
5. `InitTransfer` event emitted with `tokenAddr`.
6. NEAR relayer submits proof to NEAR bridge via `fin_transfer`.
7. NEAR bridge finds `token_decimals` entry (still present), denormalizes amount, calls `send_tokens` → mints/releases 1000 tokens to `alice.near`.
8. Result: EVM bridge holds 1000 tokens that were never burned; NEAR supply increased by 1000 unbacked tokens.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L120-127)
```text
    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-412)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L427-437)
```text
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

**File:** near/omni-bridge/src/lib.rs (L705-745)
```rust
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```
