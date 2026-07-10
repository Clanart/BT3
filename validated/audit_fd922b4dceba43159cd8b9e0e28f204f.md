### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Inflates Cross-Chain Supply — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` calls `safeTransferFrom` with the caller-supplied `amount` but emits the `InitTransfer` event (and publishes the Wormhole message) using that same unverified `amount`. For fee-on-transfer or deflationary ERC-20 tokens, the bridge receives fewer tokens than `amount`, yet the destination chain credits the full `amount`, creating unbacked supply that can be redeemed to drain other depositors' funds.

---

### Finding Description

In `OmniBridge.initTransfer`, when the token is neither a bridge token nor a custom-minter token, the contract executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-controlled input
);
``` [1](#0-0) 

Immediately after, without checking the actual post-transfer balance, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // same unverified input
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

In the Wormhole variant, `initTransferExtension` additionally publishes a Wormhole message encoding the same unverified `amount`:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

For a fee-on-transfer token (e.g., one that deducts 1% on every transfer), `safeTransferFrom(sender, bridge, 1000)` deposits only 990 tokens into the bridge, but the event and Wormhole message record `1000`. The NEAR side processes the `InitTransfer` event and mints 1000 vouchers, while the EVM bridge holds only 990 tokens.

The same unverified `amount` is also forwarded through `initTransferExtension` for any extension logic: [4](#0-3) 

---

### Impact Explanation

This is a **High** severity accounting corruption issue that breaks bridge collateralization. The destination chain mints more vouchers than tokens actually locked on the EVM side. When multiple users deposit the same deflationary token, the first user to withdraw can redeem their inflated vouchers, draining tokens that belong to later depositors. This constitutes indirect theft of bridged assets from other users.

---

### Likelihood Explanation

Any ERC-20 token with a transfer fee (e.g., USDT on some configurations, PAXG, STA, or any rebasing/deflationary token) triggers this path. The attacker only needs to call `initTransfer` as an unprivileged user with such a token. No special role or privileged access is required. The token does not need to be malicious — it only needs to implement a standard transfer fee.

---

### Recommendation

Measure the actual received amount by comparing the contract's token balance before and after the `safeTransferFrom` call, and use the delta as the canonical `amount` for the event and cross-chain message:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
uint128 actualReceived = uint128(balanceAfter - balanceBefore);
// use actualReceived instead of amount in the event and extension call
```

Apply the same fix to the `customMinters` path (lines 394–403) where `safeTransferFrom` is also called with the unverified `amount`.

---

### Proof of Concept

1. Deploy or use an existing ERC-20 token that charges a 10% fee on every `transferFrom` (e.g., a simple deflationary token).
2. Approve the `OmniBridge` contract to spend 1000 tokens.
3. Call `initTransfer(tokenAddress, 1000, 0, 0, "near-recipient.near", "")`.
4. The bridge receives 900 tokens (after 10% fee), but emits `InitTransfer(..., amount=1000, ...)`.
5. The NEAR bridge processes the event and mints 1000 vouchers to `near-recipient.near`.
6. The attacker bridges back 1000 vouchers via `finTransfer` on NEAR → EVM.
7. The EVM bridge attempts to release 1000 tokens but only holds 900, either reverting (freezing funds) or — if other users have deposited the same token — paying out 1000 tokens and stealing 100 from other depositors. [5](#0-4)

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-136)
```text
            Borsh.encodeUint128(amount),
```
