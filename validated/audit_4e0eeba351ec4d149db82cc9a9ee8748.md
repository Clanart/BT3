### Title
`nativeFee` ETH and Directly-Sent ETH Are Permanently Locked in `OmniBridge` With No Recovery Mechanism - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge` collects a `nativeFee` in ETH on every `initTransfer` call and also exposes a bare `receive() external payable {}` function. Neither the accumulated fee ETH nor any ETH sent directly to the contract can ever be recovered: there is no `sweep`, `withdraw`, or ETH-rescue function anywhere in the contract.

---

### Finding Description

`initTransfer` is `payable` and accepts ETH in two roles:

1. **Bridge amount** (when `tokenAddress == address(0)`): `amount` ETH is locked in the contract to fund future `finTransfer` payouts.
2. **`nativeFee`**: always deducted from `msg.value` before computing `extensionValue`. [1](#0-0) 

`extensionValue = msg.value - nativeFee` (ERC-20 path) or `msg.value - amount - nativeFee` (ETH path) is the only portion forwarded onward. In `OmniBridgeWormhole`, `initTransferExtension` passes only `value` (i.e., `extensionValue`) to Wormhole's `publishMessage`: [2](#0-1) 

The `nativeFee` portion is **never forwarded anywhere**. It silently accumulates in the contract's ETH balance on every bridge initiation.

Additionally, the contract declares a bare `receive` function: [3](#0-2) 

This means any ETH sent directly to the contract (e.g., by a user who mistakenly calls the contract address) is also permanently trapped.

A full audit of all admin functions in `OmniBridge.sol` confirms there is no `sweep`, `rescue`, or ETH-withdrawal function: [4](#0-3) 

---

### Impact Explanation

Every `initTransfer` call that includes a non-zero `nativeFee` permanently locks that ETH in the contract. Over the protocol's lifetime this accumulates into a material sum of irrecoverable protocol revenue. Any user who accidentally sends ETH directly to the contract address also loses those funds permanently. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user or protocol funds in bridge flows**.

---

### Likelihood Explanation

**High.** The `nativeFee` lock is not accidental — it is triggered by the normal, intended bridge flow on every ERC-20 or ETH `initTransfer` call where the caller supplies a non-zero `nativeFee`. No special attacker action is required; ordinary protocol usage is sufficient to continuously grow the locked balance. The bare `receive()` additionally exposes the contract to accidental ETH sends from wallets or other contracts.

---

### Recommendation

Add an admin-only ETH sweep function, analogous to the fix applied to the Bulker contract in the referenced report:

```solidity
function sweepEth(address payable to) external onlyRole(DEFAULT_ADMIN_ROLE) {
    (bool success, ) = to.call{value: address(this).balance}("");
    require(success, "ETH sweep failed");
}
```

If `nativeFee` is intended as protocol revenue, consider forwarding it to a designated fee recipient inside `initTransfer` rather than leaving it in the contract balance. Also consider removing the bare `receive()` or replacing it with a revert to prevent accidental ETH deposits.

---

### Proof of Concept

1. User calls `initTransfer(usdcAddress, 1000e6, 0, 0.001 ether, "alice.near", "")` sending `msg.value = 0.001 ether` as `nativeFee`.
2. Inside `initTransfer`: `extensionValue = msg.value - nativeFee = 0`.
3. `initTransferExtension` is called with `value = 0`; Wormhole receives `0` ETH.
4. The `0.001 ether` `nativeFee` remains in `OmniBridge`'s balance.
5. After 10,000 such calls at `0.001 ether` each, `10 ETH` is locked in the contract.
6. No admin function exists to recover it; the only path is a UUPS upgrade to add a sweep function.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L387-393)
```text
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-596)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }

    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }

    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }

    receive() external payable {}

    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }

    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L143-147)
```text
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```
