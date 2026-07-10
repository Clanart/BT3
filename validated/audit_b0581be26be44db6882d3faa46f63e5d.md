### Title
`nativeFee` ETH Permanently Locked in `OmniBridge` with No Withdrawal Mechanism — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

Every call to `OmniBridge.initTransfer` retains the user-supplied `nativeFee` amount of ETH inside the contract. Only the remaining `extensionValue` (i.e., `msg.value - nativeFee`, or `msg.value - amount - nativeFee` for native-ETH transfers) is forwarded to the downstream extension (Wormhole). No withdrawal function exists for native ETH, so all accumulated `nativeFee` ETH — and any ETH sent directly via the bare `receive()` — is permanently locked.

---

### Finding Description

In `OmniBridge.initTransfer`, the `nativeFee` parameter is subtracted from `msg.value` to compute `extensionValue`:

```solidity
// ERC-20 path
extensionValue = msg.value - nativeFee;          // line 393

// Native-ETH path
extensionValue = msg.value - amount - nativeFee; // line 391
```

Only `extensionValue` is forwarded to `initTransferExtension`:

```solidity
initTransferExtension(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message,
    extensionValue          // ← nativeFee is NOT included
);
``` [1](#0-0) 

In `OmniBridgeWormhole.initTransferExtension`, only `value` (i.e., `extensionValue`) is sent to Wormhole:

```solidity
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
``` [2](#0-1) 

The `nativeFee` portion therefore stays in the `OmniBridge` contract after every `initTransfer` call. Additionally, the contract exposes a bare `receive()`:

```solidity
receive() external payable {}
``` [3](#0-2) 

Searching the entire contract reveals **no `withdraw` function** for native ETH. The only ETH egress path is `finTransfer` when `tokenAddress == address(0)`, which sends `payload.amount` to the designated recipient — it does not drain the `nativeFee` pool. [4](#0-3) 

---

### Impact Explanation

Every `initTransfer` call with a non-zero `nativeFee` permanently deposits ETH into the contract with no recovery path. Over the lifetime of the bridge, this accumulates into a growing pool of irrecoverable protocol funds. Any ETH sent directly to the contract via `receive()` is similarly locked. The only escape is a UUPS contract upgrade — exactly the situation described in the reference report for the Entropy contract.

This matches the allowed impact: **Permanent freezing / irrecoverable lock of protocol funds in bridge flows.**

---

### Likelihood Explanation

`initTransfer` is the primary user-facing entry point for every outbound bridge transfer. The `nativeFee` parameter is a required argument and is expected to be non-zero in normal operation (it covers relayer costs on the destination chain, as evidenced by its inclusion in the Wormhole payload at line 137 of `OmniBridgeWormhole.sol`). [5](#0-4) 

Every bridge user who pays a `nativeFee` contributes to the locked balance. No special attacker action is required — the lock is a structural property of the contract.

---

### Recommendation

**Short term**: Add an authorized withdrawal function for accumulated native ETH, callable only by the `DEFAULT_ADMIN_ROLE`:

```solidity
function withdrawNative(address payable to, uint256 amount)
    external onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool ok, ) = to.call{value: amount}("");
    if (!ok) revert FailedToSendEther();
}
```

**Long term**: Track `nativeFee` accrual in a dedicated storage variable (analogous to `accruedPythFeesInWei` in the Entropy contract) so the locked amount is auditable on-chain, and ensure the withdrawal path is covered by integration tests.

---

### Proof of Concept

1. Alice calls `OmniBridge.initTransfer(USDC, 100e6, 0, 1e15, "alice.near", "")` with `msg.value = 1e15` (1 milli-ETH as `nativeFee`).
2. Inside `initTransfer`: `extensionValue = 1e15 - 1e15 = 0`. Wormhole receives `0` ETH. The `1e15` wei stays in the contract.
3. After 10,000 such transfers the contract holds 10 ETH of locked `nativeFee`.
4. The Pyth/Omni team attempts to recover the ETH — no function exists to do so.
5. A UUPS upgrade is required to add a withdrawal path, during which the funds remain inaccessible. [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-322)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-138)
```text
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L143-144)
```text
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
```
