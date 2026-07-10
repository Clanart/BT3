### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Enables Bridge Undercollateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.initTransfer` records and broadcasts the caller-supplied `amount` as the canonical transfer value without verifying the actual balance increase received by the contract. For fee-on-transfer ERC20 tokens, the bridge holds less collateral than it has committed to release on the destination chain, progressively undercollateralizing the bridge vault.

### Finding Description

In `OmniBridge.initTransfer`, when the token is a plain ERC20 (not a bridge token and not a custom minter), the contract pulls tokens from the caller and then immediately emits the `InitTransfer` event — and, in the Wormhole variant, publishes a cross-chain message — using the caller-supplied `amount` verbatim:

```solidity
// OmniBridge.sol lines 407-412
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← requested amount, not verified received amount
    );
}
``` [1](#0-0) 

Immediately after, the event is emitted with the same unverified `amount`:

```solidity
// OmniBridge.sol lines 427-436
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← still the caller-supplied value
    ...
);
``` [2](#0-1) 

In `OmniBridgeWormhole`, `initTransferExtension` encodes this same `amount` into the Wormhole message that NEAR consumes:

```solidity
// OmniBridgeWormhole.sol lines 135-137
Borsh.encodeUint64(originNonce),
Borsh.encodeUint128(amount),   // ← unverified amount published cross-chain
Borsh.encodeUint128(fee),
``` [3](#0-2) 

No balance-before / balance-after check is performed anywhere between the `safeTransferFrom` call and the event/message emission.

### Impact Explanation

For any fee-on-transfer ERC20 token (e.g., tokens with deflationary mechanics, reflection tokens, or tokens with configurable transfer fees), the bridge contract receives `amount - fee_taken` but records and broadcasts `amount`. The NEAR side processes the full `amount` and mints or releases that many tokens to the recipient. The EVM bridge vault is therefore short by `fee_taken` per transfer. Repeated transfers drain the vault's real balance below its recorded obligations, eventually making it impossible for legitimate users to bridge tokens back — a permanent, irrecoverable lock of user funds. This matches the allowed impact: **"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."**

### Likelihood Explanation

`initTransfer` is a fully public, permissionless function callable by any token holder. The bridge does not maintain a whitelist of accepted ERC20 tokens for the lock-and-release path; any address that is not in `isBridgeToken` and not in `customMinters` falls through to the vulnerable branch. Fee-on-transfer tokens are a well-known, deployed token class (e.g., STA, PAXG, tokens with configurable fees). An attacker can deliberately use such a token to drain the bridge's collateral for that token over many small transfers, or the issue can arise organically when a legitimate fee-on-transfer token is bridged.

### Recommendation

Record the contract's token balance before and after the `safeTransferFrom` call, and use the actual balance increase as the canonical `amount` for the event and cross-chain message:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
    require(actualReceived == amount, "Fee-on-transfer tokens not supported");
    // OR: use actualReceived as the amount going forward
}
```

Either reject fee-on-transfer tokens outright (simplest and safest), or propagate `actualReceived` through `initTransferExtension` and the emitted event instead of `amount`.

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC20 token `FeeToken` with a 1% transfer fee. It is not registered as a bridge token or custom minter.
2. Attacker (or any user) calls `OmniBridge.initTransfer(FeeToken, 1000, 0, 0, "recipient.near", "")`.
3. `safeTransferFrom` pulls 1000 tokens from the caller; the token contract deducts 1% fee, so the bridge receives only 990.
4. The contract emits `InitTransfer(..., amount=1000, ...)` and (in the Wormhole variant) publishes a Wormhole message encoding `amount=1000`.
5. The NEAR bridge processes the message and releases/mints 1000 tokens to `recipient.near`.
6. The EVM bridge vault holds only 990 `FeeToken` but has committed to 1000.
7. Repeating this 100 times with `amount=1000` each time: bridge vault holds ~99,000 tokens but has committed to 100,000 — a 1,000-token shortfall.
8. When the 100th user tries to bridge back 1000 tokens from NEAR to EVM, the `finTransfer` call will attempt `safeTransfer(recipient, 1000)` but the vault is short, causing the transfer to revert and permanently locking the last user's funds. [4](#0-3) [5](#0-4)

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```
