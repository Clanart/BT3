### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Emits Inflated Amount, Enabling Unbacked Cross-Chain Minting — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.initTransfer()` uses the caller-supplied `amount` parameter directly in the `InitTransfer` event and in the cross-chain message payload after calling `safeTransferFrom`. For fee-on-transfer ERC20 tokens, the contract actually receives fewer tokens than `amount`, but the cross-chain message claims the full `amount` was locked. The NEAR bridge processes this message and mints/releases the full `amount` to the recipient, creating unbacked supply and breaking bridge collateralization.

### Finding Description

In `OmniBridge.sol`, the `initTransfer` function handles native ERC20 tokens (those that are neither bridge tokens nor custom-minter tokens) via:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← actual received may be less for fee-on-transfer tokens
    );
}
```

Immediately after, the same caller-supplied `amount` is forwarded to the cross-chain message and event without measuring the actual balance change:

```solidity
initTransferExtension(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,             // ← input param, not actual received
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
    amount,             // ← input param, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) [2](#0-1) 

In `OmniBridgeWormhole`, `initTransferExtension` publishes a Wormhole message encoding this same `amount`:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

The NEAR bridge receives this proof and uses the `amount` field to determine how many tokens to mint or release to the recipient. There is no balance-before/balance-after check anywhere in `initTransfer` to detect the shortfall.

The `else` branch (lines 406–412) has no token whitelist — any ERC20 address that is not in `isBridgeToken` and has no `customMinters` entry is accepted. `logMetadata` is also unrestricted:

```solidity
function logMetadata(address tokenAddress) external payable {
``` [4](#0-3) 

This means any user can register an arbitrary ERC20 (including a fee-on-transfer token) and immediately call `initTransfer` with it.

### Impact Explanation

For every `initTransfer` call with a fee-on-transfer token:
- EVM bridge locks `amount - transfer_fee` tokens as collateral
- Cross-chain message claims `amount` was locked
- NEAR mints `amount` tokens to the recipient
- Net shortfall per call: `transfer_fee` tokens of unbacked supply

Repeated calls accumulate unbacked supply. When users later bridge back from NEAR to EVM, the EVM bridge cannot release the full claimed amount because it holds less collateral than the total outstanding supply. This directly breaks bridge collateralization and can result in permanent freezing of funds for later redeemers.

**Impact class**: High — Balance/accounting corruption that breaks bridge collateralization.

### Likelihood Explanation

- No privileged role required; `initTransfer` is callable by any address.
- Fee-on-transfer tokens are a well-known token pattern (e.g., tokens with built-in redistribution or burn-on-transfer mechanics).
- The attacker profits by receiving `amount` tokens on NEAR while only spending `amount - transfer_fee` tokens on EVM.
- The attack is repeatable and cumulative.

### Recommendation

Measure the actual received amount using a balance-before/balance-after pattern for the non-bridge-token path:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 received = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
    require(received == amount, "ERR_FEE_ON_TRANSFER_TOKEN");
    // or: use `received` instead of `amount` in subsequent logic
}
```

Either reject fee-on-transfer tokens outright (simplest and safest), or propagate the actual `received` amount through `initTransferExtension` and the event emission instead of the input `amount`.

### Proof of Concept

1. Attacker deploys or uses an existing ERC20 token `FeeToken` with a 1% transfer fee.
2. Attacker calls `logMetadata(FeeToken)` on the EVM bridge to register the token on NEAR.
3. Attacker calls `initTransfer(FeeToken, 1_000_000, 0, 0, "near:attacker.near", "")`.
4. `safeTransferFrom` moves 1,000,000 tokens from attacker to bridge, but bridge receives only 990,000 (1% fee taken).
5. `InitTransfer` event emits `amount = 1_000_000`.
6. NEAR bridge processes the proof and mints 1,000,000 tokens to `attacker.near`.
7. Attacker has gained 10,000 unbacked tokens on NEAR.
8. Repeating this drains the bridge's collateral backing relative to outstanding NEAR-side supply.
9. Eventually, legitimate users bridging back from NEAR to EVM cannot redeem their tokens because the EVM bridge is undercollateralized. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-137)
```text
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
```
