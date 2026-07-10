### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Enables Undercollateralized Cross-Chain Minting — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` records and relays the caller-supplied `amount` to the destination chain without verifying how many tokens were actually received. For fee-on-transfer ERC-20 tokens, the bridge locks fewer tokens than it commits to release cross-chain, permanently breaking collateralization and enabling unbacked minting on NEAR.

---

### Finding Description

In `OmniBridge.initTransfer`, when the token is a plain ERC-20 (not a bridge token and not a custom-minter token), the bridge pulls tokens from the caller:

```solidity
// OmniBridge.sol lines 407–411
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
```

No balance snapshot is taken before or after this call. The function then unconditionally passes the caller-supplied `amount` to `initTransferExtension` and emits `InitTransfer`:

```solidity
// OmniBridge.sol lines 415–436
initTransferExtension(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message, extensionValue
);

emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message
);
```

The `InitTransfer` event (and the Wormhole/MPC cross-chain message built from it) carries the original `amount`. The NEAR bridge processes this message and mints or releases exactly `amount` tokens to the recipient. The EVM bridge, however, only holds `amount − transferFee` tokens as collateral.

The same accounting gap exists in the `customMinters` path (lines 395–403): `safeTransferFrom` delivers `amount − fee` to the custom minter, but `ICustomMinter.burn(tokenAddress, amount)` is called with the full `amount`, either reverting or burning more than was received depending on the minter's implementation.

---

### Impact Explanation

Every `initTransfer` call with a fee-on-transfer token creates a collateral shortfall equal to the transfer fee. The NEAR side mints `amount` tokens backed by only `amount − fee` EVM-side collateral. Repeated calls drain the bridge's reserves. When legitimate users later bridge back from NEAR to EVM, the bridge cannot fulfill withdrawals — funds are permanently frozen for those users. This matches the allowed impact: **"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value"** and **"Permanent freezing / irrecoverable lock of user or protocol funds."**

---

### Likelihood Explanation

Fee-on-transfer tokens (e.g., PAXG, STA, tokens with built-in reflection mechanics) are a well-known ERC-20 variant. The bridge does not whitelist token types; any ERC-20 that is not in `isBridgeToken` and has no `customMinters` entry falls into the vulnerable branch. An unprivileged user only needs to call `initTransfer` with such a token — no special role or key is required.

---

### Recommendation

Capture the bridge's token balance before and after the `safeTransferFrom` call and use the delta as the authoritative transferred amount:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "fee-on-transfer token not supported");
// or: use actualReceived as the amount forwarded cross-chain
```

Apply the same pattern to the `customMinters` path (lines 395–403), checking the minter's received balance before calling `burn`.

---

### Proof of Concept

1. Deploy or use any ERC-20 that deducts a 1% fee on every `transferFrom` (e.g., a reflection token). Ensure it is registered in `OmniBridge` as a plain token (not in `isBridgeToken`, no `customMinters` entry).
2. Call `OmniBridge.initTransfer(tokenAddress, 1000e18, 0, nativeFee, nearRecipient, "")`.
3. The bridge receives `990e18` tokens (1% fee deducted by the token contract).
4. `InitTransfer` event is emitted with `amount = 1000e18`.
5. The cross-chain message (Wormhole VAA or MPC signature) carries `amount = 1000e18`.
6. NEAR bridge finalizes the transfer and mints/releases `1000e18` tokens to `nearRecipient`.
7. The EVM bridge is now undercollateralized by `10e18` tokens per call.
8. Repeat to exhaust reserves; subsequent EVM-side `finTransfer` calls for NEAR→EVM transfers will fail with insufficient balance, permanently locking those users' funds.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-403)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L407-411)
```text
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-436)
```text
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
