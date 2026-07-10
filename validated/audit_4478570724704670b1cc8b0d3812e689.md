### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Breaks Bridge Collateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` records and broadcasts the caller-supplied `amount` for native ERC20 tokens without verifying the actual amount received after the `safeTransferFrom` call. For fee-on-transfer (FoT) or deflationary ERC20 tokens, the bridge escrows fewer tokens than it reports cross-chain, permanently breaking collateralization.

---

### Finding Description

In `OmniBridge.initTransfer`, when the token is a plain ERC20 (neither a bridge token nor a custom-minter token), the transfer is executed and the original `amount` argument is forwarded verbatim to both the cross-chain message and the on-chain event:

```solidity
// OmniBridge.sol lines 406-412
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← requested amount, not actual received
    );
}
```

Immediately after, the unverified `amount` is passed to `initTransferExtension` and emitted:

```solidity
// OmniBridge.sol lines 415-436
initTransferExtension(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← still the caller-supplied value
    ...
);

emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← same unverified value
    ...
);
```

In `OmniBridgeWormhole.initTransferExtension`, this `amount` is encoded directly into the Wormhole cross-chain message:

```solidity
// OmniBridgeWormhole.sol lines 136-137
Borsh.encodeUint128(amount),
Borsh.encodeUint128(fee),
```

No before/after balance snapshot is taken anywhere in the call path to reconcile the actual received amount.

---

### Impact Explanation

For a FoT token with a `k%` transfer fee:

- Bridge escrows `amount × (1 - k/100)` tokens.
- Cross-chain message claims `amount` tokens were deposited.
- The destination chain mints or releases `amount` tokens to the recipient.
- The bridge's EVM-side reserve is now short by `amount × k/100` per transfer.

Repeated use inflates the circulating supply of the bridged asset beyond what the bridge can redeem. When users bridge back, `finTransfer` attempts `safeTransfer(recipient, amount)` against a depleted reserve, causing permanent fund lock or failed redemptions for honest users. This directly breaks bridge collateralization.

---

### Likelihood Explanation

The bridge is permissionless: any ERC20 token whose address is not in `isBridgeToken` and has no `customMinters` entry falls into the vulnerable code path. A user only needs to call `initTransfer` with a FoT token address. No privileged access is required. FoT tokens (e.g., PAXG, STA, tokens with reflection mechanics) exist on mainnet and are standard ERC20-compatible.

---

### Recommendation

Capture the actual received amount using a before/after balance check, and use that value for the cross-chain message and event:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
    amount = uint128(balanceAfter - balanceBefore); // actual received
}
```

This mirrors the pattern already used in `BathToken._deposit()` in the referenced Rubicon report. Alternatively, maintain an explicit token allowlist so that only tokens known not to have transfer fees are accepted as native ERC20 collateral.

---

### Proof of Concept

1. Deploy a FoT ERC20 token `FOT` that deducts 10% on every `transferFrom`.
2. Ensure `FOT` is not in `isBridgeToken` and has no `customMinters` entry (the plain ERC20 path).
3. Call `OmniBridge.initTransfer(FOT, 1000, 0, nativeFee, "alice.near", "")`.
4. `safeTransferFrom` moves 1000 FOT from the caller; the bridge receives 900 FOT (10% fee deducted).
5. `InitTransfer` event and Wormhole message both record `amount = 1000`.
6. NEAR side processes the message and mints/releases 1000 FOT-equivalent tokens to `alice.near`.
7. Alice bridges back 1000 tokens; `finTransfer` calls `IERC20(FOT).safeTransfer(alice, 1000)`.
8. The bridge only holds 900 FOT → `safeTransfer` reverts → Alice's funds are permanently locked.
9. Each such round-trip inflates unbacked supply by 100 FOT and depletes the bridge reserve.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-141)
```text
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
```
