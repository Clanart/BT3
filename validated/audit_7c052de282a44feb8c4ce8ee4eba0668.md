### Title
Fee-on-Transfer Token Accounting Discrepancy Breaks Bridge Collateralization - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.initTransfer` does not verify the actual token amount received after `safeTransferFrom`. For fee-on-transfer ERC20 tokens, the contract receives fewer tokens than `amount`, but emits `InitTransfer` with the full `amount`. The NEAR bridge processes this event and credits the user with the full `amount`, allowing them to withdraw more tokens than were ever deposited, draining the vault.

### Finding Description
In `OmniBridge.initTransfer`, when a native (non-bridge, non-custom-minter) ERC20 token is deposited, the code executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
``` [1](#0-0) 

Immediately after, the event is emitted using the caller-supplied `amount` — not the actual balance delta received by the contract:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // <-- caller-supplied, not verified against actual receipt
    ...
);
``` [2](#0-1) 

There is no `balanceOf(address(this))` snapshot taken before and after the transfer to confirm the real received amount. `SafeERC20.safeTransferFrom` only guards against non-reverting failures; it does not protect against fee-on-transfer tokens silently delivering less than `amount`.

### Impact Explanation
For any fee-on-transfer ERC20 token bridged through the EVM vault:

1. User calls `initTransfer(token, 1000, ...)`. Token has a 1% transfer fee.
2. Contract receives 990 tokens; event records `amount = 1000`.
3. NEAR bridge indexes the `InitTransfer` event and locks a credit of 1000 tokens for the user on the destination chain.
4. User claims 1000 tokens on NEAR (or another destination chain).
5. When bridging back, the bridge attempts to release 1000 tokens from the EVM vault, but only 990 were ever deposited.

Repeated over many transfers, the vault becomes undercollateralized. The first users to bridge back receive full value; later users cannot withdraw because the vault is short. This constitutes **balance/accounting corruption that breaks bridge collateralization** and enables **direct theft of other users' deposited assets** from the vault.

### Likelihood Explanation
Any unprivileged user can trigger this by initiating a bridge transfer with a fee-on-transfer ERC20 token. The bridge does not maintain a whitelist of allowed tokens — any ERC20 can be bridged as a "native" token. Fee-on-transfer tokens (e.g., USDT on some chains, deflationary tokens, rebasing tokens with fees) are common in the wild. No special access or privileged role is required; the entry point is the public `initTransfer` function.

### Recommendation
Capture the contract's token balance before and after the `safeTransferFrom` call and use the actual delta as the credited amount:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "Fee-on-transfer token not supported");
```

Alternatively, explicitly document and enforce that fee-on-transfer tokens are not supported, and add a token registry check to reject them at registration time.

### Proof of Concept
1. Deploy a standard ERC20 with a 1% fee-on-transfer mechanic.
2. Register it with the OmniBridge as a native token (not a bridge token, not a custom minter).
3. Call `OmniBridge.initTransfer(feeToken, 10_000, 0, 0, "recipient.near", "")` after approving 10_000 tokens.
4. `safeTransferFrom` transfers 10_000 from the user; the token contract deducts 1% fee, so the bridge vault receives 9_900.
5. The `InitTransfer` event is emitted with `amount = 10_000`.
6. The NEAR bridge relayer picks up the event and credits the user with 10_000 tokens on NEAR.
7. The user bridges 10_000 tokens back to EVM via `finTransfer`.
8. `OmniBridge.finTransfer` calls `IERC20(token).safeTransfer(recipient, 10_000)` — but the vault only holds 9_900, causing the transfer to fail or, if other users have deposited, draining their funds. [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-355)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L427-436)
```text
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
