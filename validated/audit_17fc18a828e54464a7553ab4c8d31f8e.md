### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Causes Bridge Undercollateralization — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initTransfer` function in `OmniBridge.sol` records and emits the caller-supplied `amount` parameter without accounting for the actual tokens received. For fee-on-transfer ERC20 tokens, the bridge receives `amount - transferFee` but signs and emits a cross-chain transfer for the full `amount`. This breaks bridge collateralization: the EVM vault holds less than what the destination chain will release, enabling a user to extract more value than was deposited.

---

### Finding Description

In `OmniBridge.sol`, the `initTransfer` path for native (non-bridge, non-custom-minter) ERC20 tokens is:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // requested amount, not actual received
);
``` [1](#0-0) 

Immediately after, the full `amount` is emitted in the `InitTransfer` event:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // not the actual received amount
    ...
);
``` [2](#0-1) 

The NEAR bridge processes this event and signs a release of `amount` tokens on the destination chain. When `finTransfer` is later called on EVM (for the reverse direction), the bridge releases the full signed `amount` from its vault:

```solidity
} else {
    IERC20(payload.tokenAddress).safeTransfer(
        payload.recipient,
        payload.amount
    );
}
``` [3](#0-2) 

There is no balance-before/balance-after check anywhere in `initTransfer` to measure the actual received amount. [4](#0-3) 

---

### Impact Explanation

**High — accounting corruption that breaks bridge collateralization.**

For every `initTransfer` call with a fee-on-transfer token:
- The EVM vault receives `amount - fee` tokens.
- The cross-chain message records `amount`.
- The destination chain mints or releases `amount` tokens to the recipient.

The vault is now short by `fee` tokens per transfer. When users bridge back from the destination chain to EVM, `finTransfer` attempts to release the full signed `amount` from the vault. Once the deficit accumulates, the vault cannot satisfy legitimate redemptions, causing permanent freezing of other users' funds or failed settlements — matching the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds"* and *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization."*

---

### Likelihood Explanation

**Medium.** Fee-on-transfer tokens are a well-known ERC20 variant (e.g., tokens with deflationary mechanics, PAXG, STA, and others). The `initTransfer` function accepts any arbitrary `tokenAddress` with no whitelist restriction on native tokens — any unprivileged user can call it with a fee-on-transfer token. No special access or privileged role is required. [5](#0-4) 

---

### Recommendation

Measure the actual received amount using a balance snapshot before and after the `safeTransferFrom` call, and use the delta as the canonical transfer amount in the event and cross-chain payload:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// use actualReceived instead of amount in the event and payload
```

---

### Proof of Concept

1. A fee-on-transfer token `FOT` charges a 1% fee on every transfer.
2. Alice calls `initTransfer(FOT, 1000, 0, 0, "near:alice", "")` on EVM.
3. `safeTransferFrom` moves `1000` FOT from Alice, but the bridge vault receives only `990` FOT (1% fee deducted by the token contract).
4. `InitTransfer` event is emitted with `amount = 1000`.
5. The NEAR bridge processes the event and mints/releases `1000` FOT-equivalent tokens to Alice on NEAR.
6. Alice now holds `1000` tokens on NEAR while the EVM vault only holds `990`.
7. Alice (or any user) bridges `1000` tokens back from NEAR to EVM.
8. `finTransfer` is called on EVM; the bridge attempts `safeTransfer(alice, 1000)` but only has `990` in the vault.
9. The transfer either reverts (freezing Alice's funds) or, if other users' deposits cover the shortfall, drains their collateral — breaking bridge collateralization permanently. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
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
