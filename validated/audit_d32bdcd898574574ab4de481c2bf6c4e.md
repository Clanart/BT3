### Title
Excess Native ETH Sent to `initTransfer` Is Permanently Locked in the Bridge Contract - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initTransfer` function in `OmniBridge.sol` is `payable` and accepts `msg.value` without enforcing an exact equality check against the required amount. Any ETH sent beyond the required `amount + nativeFee` (for native ETH transfers) or beyond `nativeFee` (for ERC20 transfers) is silently computed into `extensionValue` and passed to `initTransferExtension`, which is an empty `internal virtual {}` stub in the base contract. The excess ETH is permanently locked in the bridge contract with no user-accessible recovery path.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` computes `extensionValue` as follows:

```solidity
// tokenAddress == address(0): native ETH bridge
extensionValue = msg.value - amount - nativeFee;   // line 391

// tokenAddress != address(0): ERC20 bridge
extensionValue = msg.value - nativeFee;             // line 393
``` [1](#0-0) 

Solidity 0.8+ arithmetic means the subtraction reverts if `msg.value` is too small (underflow protection), but there is **no upper-bound check** — no `require(msg.value == amount + nativeFee)` or equivalent. If a user sends any ETH above the minimum required, the surplus is silently captured in `extensionValue`.

`extensionValue` is then forwarded to `initTransferExtension`:

```solidity
initTransferExtension(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message,
    extensionValue
);
``` [2](#0-1) 

In the base contract, `initTransferExtension` is a no-op:

```solidity
function initTransferExtension(...) internal virtual {}
``` [3](#0-2) 

The excess ETH is absorbed into the contract balance. There is no refund, no per-user accounting of excess deposits, and no user-callable withdrawal function to recover it. The same pattern applies to `initTransfer1155`. [4](#0-3) 

---

### Impact Explanation

Any ETH sent above the exact required amount during `initTransfer` or `initTransfer1155` is permanently locked in the `OmniBridge` contract. The user loses the excess with no recovery path. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user funds in bridge flows.**

---

### Likelihood Explanation

This is realistically triggered by:
- Users manually constructing transactions and slightly overestimating the required ETH.
- Frontend/wallet rounding errors when computing `amount + nativeFee`.
- Users retrying a failed transaction with a higher value.
- Integrating contracts that pass a rounded or estimated `msg.value`.

No privileged access is required. Any unprivileged bridge user calling `initTransfer` with `msg.value > amount + nativeFee` (native ETH path) or `msg.value > nativeFee` (ERC20 path) triggers the lock.

---

### Recommendation

Enforce an exact equality check at the start of `initTransfer` and `initTransfer1155`:

```solidity
// For native ETH transfers (tokenAddress == address(0)):
require(msg.value == uint256(amount) + uint256(nativeFee), "Exact ETH required");

// For ERC20 transfers:
require(msg.value == uint256(nativeFee), "Exact native fee required");
```

Alternatively, refund any excess ETH to `msg.sender` at the end of the function:

```solidity
uint256 excess = msg.value - uint256(amount) - uint256(nativeFee);
if (excess > 0) {
    (bool ok, ) = msg.sender.call{value: excess}("");
    require(ok, "Refund failed");
}
```

---

### Proof of Concept

1. `founderHeroPrice` analog: `nativeFee = 0.1 ether`, `amount = 1 ether` (native ETH bridge).
2. User calls `initTransfer(address(0), 1 ether, 0, 0.1 ether, "near:recipient", "")` with `msg.value = 1.15 ether` (0.05 ETH excess due to UI rounding).
3. Contract computes `extensionValue = 1.15 ether - 1 ether - 0.1 ether = 0.05 ether`.
4. `initTransferExtension(...)` is called — it is an empty stub, so `extensionValue` is discarded.
5. The 0.05 ETH excess remains in the `OmniBridge` contract balance.
6. The user has no function to call to recover it. The ETH is permanently locked. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L369-371)
```text
    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal virtual {}
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-450)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
```
