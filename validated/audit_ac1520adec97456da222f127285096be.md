### Title
Fee-on-Transfer Token Accounting Discrepancy Breaks Bridge Collateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

The EVM `OmniBridge.initTransfer` function records the caller-supplied `amount` as the canonical bridged value and emits it in the `InitTransfer` event, but it never verifies how many tokens the contract actually received. For fee-on-transfer ERC20 tokens, the bridge vault receives `amount - transferFee` while the cross-chain message commits to releasing the full `amount` on the destination chain. This creates a systematic, cumulative undercollateralization of the EVM vault that any unprivileged user can trigger.

### Finding Description

In `OmniBridge.initTransfer`, the standard (non-bridge, non-custom-minter) ERC20 path is:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← caller-controlled, not verified post-transfer
);
```

Immediately after, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // ← same caller-supplied value, not actual received amount
    fee,
    nativeFee,
    recipient,
    message
);
```

The NEAR bridge (or any destination chain bridge) processes the `InitTransfer` event and releases `amount` tokens to the recipient. There is no balance-before/balance-after check to confirm the vault actually received `amount` tokens.

For a fee-on-transfer token (e.g., PAXG with its 0.02% transfer fee, or any deflationary ERC20), the vault receives `amount × (1 - feeRate)` but the cross-chain commitment is for the full `amount`. Every such transfer leaves the vault short by `amount × feeRate`.

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

Each `initTransfer` call with a fee-on-transfer token creates a deficit in the EVM vault. The deficit is cumulative: after N transfers of size `amount` each, the vault is short by `N × amount × feeRate`. When users later bridge back from NEAR to EVM, the EVM bridge attempts to `safeTransfer` the full recorded amount but the vault no longer holds it. The last users to redeem will find the vault insolvent — their funds are permanently locked or irrecoverable on the EVM side.

### Likelihood Explanation

**Medium.** No token whitelist or allowlist exists in `initTransfer`; any ERC20 address is accepted. Fee-on-transfer tokens are a well-established token class (PAXG, STA, USDT on some chains with fees enabled, etc.). Any unprivileged user can call `initTransfer` with such a token. The deficit grows with every transfer and is not self-correcting.

### Recommendation

Record the actual received amount by comparing vault balance before and after the transfer:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "ERR_FEE_ON_TRANSFER_TOKEN");
```

Use `actualReceived` (not `amount`) in the `InitTransfer` event and all downstream accounting. Alternatively, maintain an explicit allowlist of supported tokens and reject fee-on-transfer tokens at the protocol level.

### Proof of Concept

1. Deploy or identify a fee-on-transfer ERC20 token `FeeToken` that deducts 2% on every transfer.
2. Approve `OmniBridge` to spend 1000 `FeeToken`.
3. Call `OmniBridge.initTransfer(FeeToken, 1000, 0, 0, nearRecipient, "")`.
4. `safeTransferFrom` executes: bridge vault receives 980 `FeeToken` (2% fee deducted).
5. `InitTransfer` event is emitted with `amount = 1000`.
6. NEAR relayer observes the event and calls `fin_transfer` on NEAR, releasing 1000 units to `nearRecipient`.
7. Bridge vault holds 980 but has committed to 1000 — a 20-token deficit.
8. Repeat step 2–7 many times; vault deficit grows linearly.
9. When a NEAR→EVM redemption for `FeeToken` is finalized, `OmniBridge.finTransfer` calls `IERC20(FeeToken).safeTransfer(recipient, amount)` but the vault is insolvent; the transfer reverts and the user's funds are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
