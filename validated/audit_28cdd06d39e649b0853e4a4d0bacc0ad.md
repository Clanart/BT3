### Title
Fee-on-Transfer Token Accounting Mismatch Breaks Bridge Collateralization - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
The EVM `OmniBridge.initTransfer` function records the user-supplied `amount` in the `InitTransfer` event rather than the actual token balance increase received by the contract. For fee-on-transfer ERC-20 tokens, the bridge receives fewer tokens than `amount`, but the NEAR side mints/releases the full `amount` to the recipient. This creates a permanent, accumulating collateral deficit that eventually prevents the last users from withdrawing their tokens on the EVM side.

### Finding Description
In `OmniBridge.initTransfer`, when the token is a plain ERC-20 (not a bridge token and not a custom minter), the contract executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // requested amount, not actual received
);
``` [1](#0-0) 

Immediately after, the function emits the `InitTransfer` event using the caller-supplied `amount` parameter — not the actual post-transfer balance increase of the contract:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // <-- not verified against actual received balance
    ...
);
``` [2](#0-1) 

The NEAR bridge's `fin_transfer_callback` reads the `amount` field directly from the proven `InitTransfer` event and uses it to determine how many tokens to release or mint to the recipient:

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [3](#0-2) 

For fee-on-transfer tokens (e.g., tokens that deduct a percentage on every `transferFrom`), the EVM bridge receives `amount - fee_deducted` but the NEAR side credits the recipient with the full `amount`. The difference is never reconciled.

There is no balance-before/balance-after check anywhere in `initTransfer` to detect the actual received quantity. [4](#0-3) 

### Impact Explanation
Every `initTransfer` call with a fee-on-transfer token creates a deficit: the EVM bridge holds `amount - transfer_fee` tokens but the NEAR side has credited `amount` tokens worth of bridged supply. The deficit accumulates linearly with each transfer. Eventually the EVM bridge's token balance is insufficient to honor all outstanding redemptions. The last users to call `finTransfer` on the EVM side will find the bridge insolvent and their funds permanently locked — matching the **High** impact class: *balance/accounting corruption that breaks bridge collateralization*.

### Likelihood Explanation
Fee-on-transfer ERC-20 tokens are a well-known token class (e.g., PAXG, STA, tokens with configurable fees). The `initTransfer` function is fully permissionless — any user can call it with any ERC-20 address. No privileged role or special condition is required. The attacker does not need to do anything beyond initiating a normal bridge transfer with such a token.

### Recommendation
Measure the actual balance increase by comparing the contract's token balance before and after the `safeTransferFrom` call, and use that delta as the canonical `amount` in the emitted event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// use actualReceived in the event and downstream logic
```

This mirrors the fix suggested in the external report (using the balance increase rather than the deposited amount) and is the same pattern already used in the Compound integration for the fee-charged case.

### Proof of Concept
1. Deploy a fee-on-transfer ERC-20 token `FeeToken` that deducts 1% on every `transferFrom`.
2. User approves `OmniBridge` for `1000` `FeeToken` and calls `initTransfer(FeeToken, 1000, 0, 0, nearRecipient, "")`.
3. `OmniBridge` receives `990` tokens (1% fee deducted), but emits `InitTransfer(..., amount=1000, ...)`.
4. A relayer submits the proof to the NEAR bridge; `fin_transfer_callback` reads `amount=1000` and releases `1000` units of the bridged token to `nearRecipient`.
5. The EVM bridge is now undercollateralized by 10 tokens.
6. Repeat 100 times: EVM bridge holds `99,000` tokens but NEAR has credited `100,000`.
7. When users bridge back from NEAR to EVM, the 100th user's `finTransfer` call on EVM will fail or partially fail because the bridge lacks sufficient `FeeToken` balance, permanently locking their funds.

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L725-725)
```rust
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
```
