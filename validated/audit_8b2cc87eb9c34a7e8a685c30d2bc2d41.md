### Title
`nativeFee` ETH Permanently Locked in `OmniBridge` — No ETH Rescue/Withdrawal Function - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.sol` accepts ETH via multiple `payable` functions but implements no ETH rescue or withdrawal mechanism. The `nativeFee` portion of every `initTransfer` call is irrecoverably locked in the contract. This is a direct structural analog to the NounsDAO `Stream` contract issue: a contract that can receive ETH but cannot release it.

---

### Finding Description

`initTransfer` is `payable` and computes an `extensionValue` that excludes `nativeFee`:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol
if (tokenAddress == address(0)) {
    extensionValue = msg.value - amount - nativeFee;   // line ~391
} else {
    extensionValue = msg.value - nativeFee;             // line ~393
}
initTransferExtension(..., extensionValue);
``` [1](#0-0) 

Only `extensionValue` is forwarded to the virtual `initTransferExtension` hook (which the Wormhole subclass uses to pay Wormhole fees). The `nativeFee` ETH is **never forwarded anywhere** — it stays in the contract balance after every call.

The base `initTransferExtension` simply reverts if `value != 0`, confirming that `extensionValue` is the only ETH the extension layer ever touches:

```solidity
function initTransferExtension(..., uint256 value) internal virtual {
    if (value != 0) { revert InvalidValue(); }
}
``` [2](#0-1) 

Additionally, `logMetadata`, `logMetadata1155`, `deployToken`, and `addCustomToken` are all declared `payable`, so any ETH sent to them is also silently retained with no release path: [3](#0-2) [4](#0-3) [5](#0-4) 

There is no `rescueETH`, `withdrawETH`, or any other ETH-release function anywhere in `OmniBridge.sol`.

---

### Impact Explanation

Every user who calls `initTransfer` with `nativeFee > 0` permanently donates that ETH to the contract. The ETH cannot be recovered by the user, the relayer, or the protocol admin. Over time, as the bridge accumulates `nativeFee` payments across all ERC-20 and native-ETH transfers, the locked ETH grows monotonically with no release mechanism. This constitutes **irrecoverable lock of user funds** in the bridge flow.

Impact category: **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flow.**

---

### Likelihood Explanation

`nativeFee` is a first-class protocol parameter documented in the public API. Any user bridging tokens and paying a native fee (the normal, intended usage) triggers this lock on every single call. Likelihood is **high** — it is not an edge case but the standard operating path.

---

### Recommendation

Add a privileged `rescueETH` function analogous to any existing `rescueERC20` pattern, allowing the admin to recover ETH that accumulates in the contract:

```solidity
function rescueETH(address payable to, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool success, ) = to.call{value: amount}("");
    if (!success) revert FailedToSendEther();
}
```

Alternatively, if `nativeFee` is intended to be forwarded to a specific relayer or fee recipient, implement that forwarding logic inside `initTransfer` or `initTransferExtension` rather than leaving the ETH stranded in the contract.

---

### Proof of Concept

1. Deploy `OmniBridge` (or `OmniBridgeWormhole`).
2. Call `initTransfer(someERC20, amount, 0, 1 ether, "recipient.near", "")` with `msg.value = 1 ether` (the `nativeFee`).
3. `extensionValue = 1 ether - 1 ether = 0` → `initTransferExtension` does not revert.
4. `address(omniBridge).balance` increases by `1 ether`.
5. No function exists to withdraw that `1 ether`. It is permanently locked.
6. Repeat for every subsequent `initTransfer` call with `nativeFee > 0`; the locked balance grows without bound. [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-138)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L234-240)
```text
    function logMetadata1155(
        address tokenAddress,
        uint256 tokenId
    ) external payable {
        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L492-506)
```text
    function initTransferExtension(
        address /*sender*/,
        address /*tokenAddress*/,
        uint64 /*originNonce*/,
        uint128 /*amount*/,
        uint128 /*fee*/,
        uint128 /*nativeFee*/,
        string calldata /*recipient*/,
        string calldata /*message*/,
        uint256 value
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
    }
```
