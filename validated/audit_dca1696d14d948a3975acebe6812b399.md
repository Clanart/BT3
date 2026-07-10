### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Emits Inflated Amount, Breaking Bridge Collateralization — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary

`OmniBridge.initTransfer` calls `safeTransferFrom(msg.sender, address(this), amount)` for plain ERC20 tokens but emits the caller-supplied `amount` in the `InitTransfer` event without verifying the actual tokens received. For fee-on-transfer tokens, the bridge receives less than `amount`, yet the NEAR side is instructed to release the full `amount`, making the bridge progressively undercollateralized.

### Finding Description

In `OmniBridge.initTransfer`, the plain-ERC20 branch (lines 406–412) executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-supplied, not validated post-transfer
);
```

Immediately after, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // same caller-supplied value, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
```

No balance-before/after check is performed. For a fee-on-transfer ERC20 (a token that deducts a percentage on every `transferFrom`), the bridge receives `amount − fee_taken` but the event records `amount`. The NEAR bridge contract processes the `InitTransfer` event and releases `amount` tokens to the recipient on NEAR, while the EVM bridge only holds `amount − fee_taken`. Each such transfer widens the collateral gap.

The same pattern is absent from the `isBridgeToken` and `customMinters` branches, but those branches mint/burn rather than custody tokens, so they are not affected. Only the plain-ERC20 custody path is vulnerable.

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

The EVM bridge holds fewer tokens than the NEAR side has released. Repeated transfers with a fee-on-transfer token drain the bridge's ERC20 reserves. Eventually, legitimate users attempting to bridge back from NEAR to EVM will find the bridge unable to release their tokens, permanently locking their funds. This matches the allowed impact: *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."*

### Likelihood Explanation

**Medium.** Fee-on-transfer tokens are a well-known ERC20 pattern (reflection tokens, auto-liquidity tokens, etc.). The bridge does not whitelist or restrict which ERC20 tokens can be used in `initTransfer`; any address that is not in `isBridgeToken` or `customMinters` falls into the plain-ERC20 path. A user or attacker only needs to initiate a transfer with such a token that has been registered on the NEAR side.

### Recommendation

Measure the actual received amount using a balance-before/after check and use that value in the event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = uint128(
    IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore
);
require(actualReceived > 0, "No tokens received");
// replace `amount` with `actualReceived` in the event and downstream logic
```

Alternatively, explicitly document and enforce that fee-on-transfer tokens are not supported by adding a check that `actualReceived == amount`.

### Proof of Concept

1. Deploy a fee-on-transfer ERC20 token `FeeToken` that deducts 1% on every `transferFrom`.
2. Register `FeeToken` on both the EVM bridge and the NEAR bridge (so it is neither a `isBridgeToken` nor a `customMinters` entry on EVM).
3. User calls `OmniBridge.initTransfer(FeeToken, 1000, 0, 0, "alice.near", "")`.
4. `safeTransferFrom` executes: bridge receives **990** tokens (1% fee deducted by the token contract).
5. `InitTransfer` event is emitted with `amount = 1000`.
6. NEAR relayer picks up the event and calls `fin_transfer` on NEAR, releasing **1000** tokens to `alice.near`.
7. Bridge is now undercollateralized by **10** tokens.
8. Repeating this 100 times with `amount = 1000` drains the bridge of **1000** tokens while NEAR has released **100,000** tokens — a 10× unbacked supply.
9. When a legitimate user tries to bridge 1000 `FeeToken` back from NEAR to EVM, the bridge cannot fulfill the release, permanently locking their funds.

---

**Root cause location:** [1](#0-0) 

**Event emission with unvalidated `amount`:** [2](#0-1) 

**`ICustomMinter` interface (not affected — mint/burn path, no custody):** [3](#0-2)

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

**File:** evm/src/common/ICustomMinter.sol (L1-7)
```text
// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity 0.8.24;

interface ICustomMinter {
    function mint(address token, address to, uint128 amount) external;
    function burn(address token, uint128 amount) external;
}
```
