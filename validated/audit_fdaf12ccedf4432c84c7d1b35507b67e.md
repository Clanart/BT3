### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Enables Bridge Undercollateralization — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` calls `safeTransferFrom` to pull tokens from the caller but emits the caller-supplied `amount` parameter in the `InitTransfer` event rather than the actual tokens received. For fee-on-transfer ERC20 tokens, the bridge contract receives fewer tokens than `amount`, yet the event records the full `amount`. The NEAR bridge consumes this event to mint or release an equivalent quantity of tokens on the destination chain, creating an unbacked surplus and permanently breaking collateralization for that token.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` handles non-bridge, non-custom-minter ERC20 tokens as follows:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← caller-controlled parameter
    );
}
``` [1](#0-0) 

`SafeERC20.safeTransferFrom` only verifies that the ERC20 `transferFrom` call returns `true`; it does not verify the actual balance delta of the bridge contract. For fee-on-transfer tokens the call succeeds and returns `true`, but the bridge receives `amount − fee_amount` tokens.

Immediately after, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // ← original parameter, not actual received amount
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

The `InitTransfer` event is the authoritative cross-chain message consumed by the NEAR bridge to determine how many tokens to mint or release on the destination chain. Because the event records the inflated `amount`, the NEAR side mints more tokens than the EVM bridge actually holds.

The same structural flaw exists in the StarkNet bridge's `init_transfer`, which checks `success` from `transfer_from` but does not measure the actual balance change before emitting `InitTransfer`:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
``` [3](#0-2) 

followed by emitting `amount` unchanged in the event. [4](#0-3) 

---

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

Every `initTransfer` call with a fee-on-transfer token creates a permanent deficit: the EVM bridge holds `amount − fee_amount` tokens while the NEAR side has minted `amount` bridge tokens. When any holder of those NEAR-side tokens bridges back to EVM, the bridge cannot satisfy the full redemption. Repeated calls accumulate the deficit. The bridge's locked-token accounting (`locked_tokens` map on NEAR) also diverges from the true on-chain balance, making the discrepancy invisible to the protocol. [5](#0-4) 

---

### Likelihood Explanation

**Medium.** Fee-on-transfer tokens are a well-known ERC20 variant (e.g., tokens with reflection mechanics or protocol fees). The bridge imposes no whitelist on which ERC20 tokens can be used with `initTransfer`; any caller can invoke the function with any ERC20 address. An attacker can deploy a custom fee-on-transfer token, register it via `logMetadata`, and immediately exploit the discrepancy. No privileged access is required. [6](#0-5) 

---

### Recommendation

Measure the actual balance change around the `safeTransferFrom` call and use that value — not the caller-supplied `amount` — in all subsequent logic and event emission:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// use actualReceived instead of amount going forward
```

Apply the same pattern in the StarkNet `init_transfer` by reading the contract's token balance before and after the `transfer_from` call and emitting the delta.

---

### Proof of Concept

1. Deploy `FeeToken`, an ERC20 that deducts a 10% fee on every `transferFrom`, crediting the fee to a fee-recipient address. The call returns `true` and emits a standard `Transfer` event for the full `amount`.
2. Call `OmniBridge.logMetadata(feeTokenAddress)` to register the token and obtain an MPC-signed metadata payload, enabling the NEAR bridge to deploy a corresponding bridge token.
3. Approve the bridge for 1 000 `FeeToken` units, then call:
   ```
   initTransfer(feeTokenAddress, 1000, 0, 0, "<near_recipient>", "")
   ```
4. `safeTransferFrom` succeeds; the bridge receives **900** tokens. The emitted `InitTransfer` event records `amount = 1000`.
5. The NEAR bridge processes the event and mints **1 000** bridge tokens to `<near_recipient>`.
6. The bridge is now undercollateralized by **100** tokens. Any subsequent redemption of those 1 000 NEAR-side tokens back to EVM will fail or drain tokens belonging to other users, permanently breaking collateralization for this token. [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-436)
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
```

**File:** starknet/src/omni_bridge.cairo (L304-307)
```text
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }
```

**File:** starknet/src/omni_bridge.cairo (L316-330)
```text
            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
                )
```

**File:** near/omni-bridge/src/lib.rs (L242-243)
```rust
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```
