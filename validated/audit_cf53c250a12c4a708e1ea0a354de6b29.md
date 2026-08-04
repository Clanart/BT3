## Finding

### Title
Unchecked ERC20 `transfer` return value in Tron `IntentGatewayV2` allows silent transfer failure while escrow is finalized - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron port of `IntentGatewayV2` releases escrowed order funds and sweeps dust using a raw low-level `.call()` to the ERC20 `transfer` selector, but only checks the boolean `success` of the *call itself* and discards the ABI-decoded return data. This is the exact bug class from the external report (`IWETH.transfer` return value not checked): a token that returns `false` on failure instead of reverting — or any address with no contract code — will make `success == true` while no tokens actually move, yet the surrounding escrow bookkeeping (`_filled`, `_orders[...]`) is finalized as if the transfer succeeded.

### Finding Description
In `withdraw()`, escrowed tokens are released to a `beneficiary` via: [1](#0-0) 

```solidity
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}

_orders[body.commitment][token] -= amount;
```

`_filled[body.commitment] = beneficiary;` is set unconditionally at the top of `withdraw()` before this loop runs, and `_orders[...][token]` is decremented right after the `.call` regardless of what the token contract's return data actually says.

The same unchecked pattern is repeated for transaction-fee redemption: [2](#0-1) 

and for dust sweeping via `SweepDust`: [3](#0-2) 

In Solidity, a low-level `.call()` returns `success = true` whenever the callee does not revert — this includes: (a) a compliant-but-failing ERC20 that returns `false` instead of reverting on failure (a well-known category of non-standard tokens), and (b) a call to an address with no deployed code at all, which trivially "succeeds" with empty returndata. Neither case is caught by checking only `success`; the actual ABI-decoded `bool` return value from `transfer` is never inspected.

This directly contrasts with the rest of the codebase, which consistently uses OpenZeppelin's `SafeERC20.safeTransfer`/`safeTransferFrom` (which does decode and check the return value) for the equivalent EVM-mainline logic in `IntentsBase.sol`: [4](#0-3) 

The Tron variant deviates from this safe pattern and reintroduces the exact unchecked-return-value defect described in the external report.

### Impact Explanation
This falls squarely under "bridge custody / intent settlement" fund-movement correctness required by the bounty scope. If a `token` used in an order's inputs/outputs is a non-reverting-on-failure ERC20 (or becomes code-less, e.g. via `SELFDESTRUCT` on a chain that still supports it, or is simply an address with no code due to a data/ABI mismatch), `withdraw()` will:
- Mark the order as permanently filled (`_filled[commitment] = beneficiary`).
- Decrement the internal escrow accounting (`_orders[commitment][token] -= amount`).
- Emit `EscrowReleased`/`EscrowRefunded`.

...all without the beneficiary actually receiving the tokens. This is a genuine loss-of-funds condition: the escrowed value becomes unrecoverable because the order is already marked filled/refunded, and the accounting no longer reflects an outstanding balance to retry the transfer.

### Likelihood Explanation
Order `token` addresses in the intents flow are attacker/solver/order-creator supplied fields (from the order struct), not restricted to a hardcoded allowlist within this function. Any order that references a non-standard ERC20 (or one deployed to be capable of "failing gracefully" by returning `false`) will trigger this path deterministically during withdrawal/refund/dust-sweep — no privileged actor, malicious relayer, or race condition is required, only a token whose `transfer` doesn't revert on failure.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` handler with `SafeERC20.safeTransfer`, matching the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol`. This ensures both call-level and return-data-level transfer failures cause a revert before `_filled`/`_orders` state is finalized.

### Proof of Concept
1. Create/register (or have a solver route funds through) an ERC20 token contract on the Tron deployment whose `transfer(address,uint256)` returns `false` on failure conditions instead of reverting (a legally-shaped but non-standard ERC20 implementation), or an address that simply has no deployed code by the time `withdraw()` runs.
2. Open an intent order using this token as an input/output and have it escrowed normally via `safeTransferFrom` (which works fine going in).
3. Trigger the fill/refund path so `withdraw()` is invoked with that token in `body.tokens`.
4. Observe: `token.call(...)` returns `success = true` (either because the token returned `false` gracefully, or the address has no code), so `TransferFailed` is never raised.
5. `_orders[commitment][token]` is decremented and `_filled[commitment]` is set, but the beneficiary's token balance is unchanged — the escrowed funds are now unrecoverable through the normal withdrawal path.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L403-409)
```text
            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
