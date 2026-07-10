### Title
Fee-on-Transfer ERC20 Token Causes Undercollateralization in EVM Bridge Locking — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter to emit the `InitTransfer` event without verifying the actual number of tokens received by the contract after `safeTransferFrom`. For fee-on-transfer ERC20 tokens, the bridge receives fewer tokens than `amount`, but the event (and therefore the cross-chain proof) records the full `amount`. The NEAR side mints/releases the full `amount` to the recipient, permanently undercollateralizing the EVM bridge vault.

---

### Finding Description

In `OmniBridge.sol`, the `initTransfer` function handles native ERC20 tokens (non-bridge, non-custom-minter) via:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-supplied
);
```

Immediately after, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // same caller-supplied value, not actual received balance
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) [2](#0-1) 

There is no balance-before/balance-after check to determine the actual tokens received. For a fee-on-transfer token (e.g., USDT with fee enabled, STA, PAXG, or any deflationary token), the bridge receives `amount - transfer_fee` but the emitted event records `amount`.

The NEAR bridge's `fin_transfer` path verifies the proof derived from this event and releases/mints the full `amount` to the recipient on NEAR: [3](#0-2) 

Conversely, when a user bridges back from NEAR to EVM, the EVM `finTransfer` path calls:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount   // derived from the inflated InitTransfer event
);
``` [4](#0-3) 

The bridge holds less than `payload.amount` for each prior deposit, so the `safeTransfer` will eventually revert for later users, permanently locking their funds.

---

### Impact Explanation

Each `initTransfer` call with a fee-on-transfer token creates a deficit of `transfer_fee` tokens in the EVM bridge vault. The deficit accumulates across all users. When users attempt to bridge back (EVM `finTransfer`), the bridge will run out of tokens before all users are served. The last users to withdraw will find their funds permanently frozen in the bridge contract, with no recovery path. This directly matches the allowed impact: **balance/accounting corruption that breaks bridge collateralization**, and **permanent freezing of user funds**.

---

### Likelihood Explanation

- Fee-on-transfer tokens are a well-known ERC20 variant. USDT has a fee mechanism that is currently set to zero but can be activated by the Tether issuer at any time.
- The bridge does not whitelist or validate tokens against a fee-on-transfer property. Any token that passes the `customMinters`/`isBridgeToken` checks falls into the vulnerable `safeTransferFrom` path.
- An unprivileged user only needs to call `initTransfer` with a fee-on-transfer token that has a valid cross-chain mapping configured. No privileged access is required to trigger the accounting discrepancy. [5](#0-4) 

---

### Recommendation

Replace the static `amount` in the `InitTransfer` event with the actual received amount, computed via a balance snapshot:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// Use actualReceived (cast to uint128) in the event and downstream logic
```

Alternatively, document and enforce that only non-fee-on-transfer tokens may be bridged, and add an on-chain check or token registry that rejects fee-on-transfer tokens at registration time.

---

### Proof of Concept

1. A fee-on-transfer ERC20 token `FeeToken` (1% fee per transfer) is registered in the Omni Bridge with a corresponding NEAR token mapping.
2. Alice calls `initTransfer(FeeToken, 1000, 0, 0, "alice.near", "")` on EVM OmniBridge.
3. `safeTransferFrom` moves 1000 tokens from Alice, but the bridge only receives 990 (1% fee taken by the token contract).
4. The `InitTransfer` event is emitted with `amount = 1000`.
5. The NEAR relayer picks up the event, generates a proof, and calls `fin_transfer` on NEAR, minting 1000 `FeeToken` equivalents to `alice.near`.
6. Alice now holds 1000 NEAR-side tokens backed by only 990 EVM-side tokens — a 10-token deficit.
7. After 100 such deposits, the bridge holds 99,000 tokens but has issued 100,000 NEAR-side tokens.
8. When the 100th user tries to bridge back to EVM, `safeTransfer(recipient, 1000)` reverts because the bridge is short. Their funds are permanently frozen. [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L1957-1966)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
```
