Based on my investigation, I found a concrete, locally-provable CEI (checks-effects-interactions) regression in the Tron port of the intent gateway, directly analogous to the Yearn "mint-before-transfer" bug class. I was unable to fully inspect the `fillOrder`/`_fillCrossChain` equivalent inside `evm/tron/contracts/apps/IntentGatewayV2.sol` before running out of tool iterations, so I limit the claim to what I fully verified: the `withdraw()` internal function and the total absence of reentrancy protection in the file.

### Title
Missing reentrancy guard + pre-effects fund transfer in `withdraw()` — (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron port of `IntentGatewayV2` (`evm/tron/contracts/apps/IntentGatewayV2.sol`) has **no `ReentrancyGuard`/`nonReentrant` anywhere in the file** (confirmed via repo-wide grep across `evm/tron/**`), unlike its EVM sibling `evm/src/apps/IntentGatewayV2.sol`, whose `placeOrder` is explicitly `nonReentrant` [1](#0-0) . In the Tron contract, `withdraw()` sends funds to the beneficiary via a raw `.call` **before** decrementing the corresponding escrow accounting entry `_orders[body.commitment][token]`, reproducing the exact "effects after interaction" defect described in the Yearn report (shares minted/state updated only after the external transfer).

### Finding Description
`withdraw()` in the Tron gateway: [2](#0-1) 

sets `_filled[body.commitment] = beneficiary` first (good), but then, for each token, performs the external transfer (`beneficiary.call{value: amount}("")` for native tokens, or `token.call(...transfer...)` for ERC-20s) and only afterward executes `_orders[body.commitment][token] -= amount`. This is a direct checks-effects-interactions violation on the per-token escrow ledger, structurally identical to `YearnV2YieldSource.supplyTokenTo` minting shares before pulling/accounting for the deposited token.

Compare this to `placeOrder`, which similarly performs external interactions (`safeTransferFrom`, `ICallDispatcher.dispatch`, `IUniswapV2Router02.swapETHForExactTokens`) and only afterward writes escrow state (`_orders[commitment][token] += reducedInputs[i].amount`, `_orders[commitment][TRANSACTION_FEES] = order.fees`) [3](#0-2) . None of these functions carry a `nonReentrant` modifier, so if any escrowed/fee asset is a token with a transfer-hook (ERC-777-style, or any token whose `transfer`/`transferFrom` can invoke recipient code), a reentrant call during that hook can re-enter `placeOrder` or `cancelOrder` for a *different* commitment while the current call's escrow bookkeeping is still stale.

By contrast, the primary EVM implementation was explicitly hardened against this same class of bug: the foundry test suite documents that `_filled[commitment]` was moved to the top of `_fillSameChain` specifically to close a reentrancy hole that let a malicious beneficiary "steal escrowed tx fees" and "steal the entire input[1] escrow" during a same-block reentrant fill [4](#0-3) [5](#0-4) . I found **no equivalent reentrancy test suite for the Tron contract**, and no `nonReentrant` guard was ever added to it, meaning the Tron port did not fully inherit the audited fix even though it superficially copies the "`_filled` set first" pattern in `withdraw()`.

### Impact Explanation
If any escrow, output, or fee token accepted by the Tron `IntentGateway` deployment has transfer-time callback semantics, an attacker acting as `order.output.beneficiary` or fee recipient could re-enter the contract mid-`withdraw()`/`placeOrder()` before escrow/fee state is finalized, potentially manipulating cross-order accounting (nonce/commitment computation, fee escrow totals) since no global reentrancy lock exists to serialize state mutation across the whole contract, unlike patterns already required and enforced on the EVM side.

### Likelihood Explanation
Medium-low confidence/likelihood: the `_filled[commitment]` unconditional write at the very top of `withdraw()` blocks the most direct single-commitment double-withdrawal, and Solidity's atomic-revert semantics prevent partial fund loss when the *same* token/commitment pair is re-processed (an underflow on `_orders[...] -= amount` reverts the whole transaction). The residual risk is narrower — cross-function/cross-commitment reentrancy while escrow-affecting external calls (Uniswap swap, dispatcher calls, raw token transfers) are outstanding — and depends on which token types governance registers as escrow/fee assets. I could not verify within the available tool budget whether `fillOrder`/`_fillSameChain`-equivalent logic in this Tron file (which I did not have iterations left to read) preserves the same early `_filled` write that the EVM version required a dedicated fix for; that is the remaining unverified piece.

### Recommendation
Add `ReentrancyGuard` (`nonReentrant`) to every state-mutating external entry point in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`placeOrder`, `cancelOrder`, and any `fillOrder`/fill-equivalent function), matching `evm/src/apps/IntentGatewayV2.sol`. Additionally, reorder `withdraw()` to decrement `_orders[body.commitment][token]` **before** issuing the external transfer for each token, fully restoring checks-effects-interactions ordering rather than relying solely on the `_filled` flag.

### Proof of Concept
Not fully constructible without further access to the Tron contract's fill-order logic (unread due to tool budget). The provable artifact is the code-level CEI violation itself:
```solidity
// evm/tron/contracts/apps/IntentGatewayV2.sol:693-701
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");   // <-- external interaction first
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}
_orders[body.commitment][token] -= amount;                 // <-- effect happens after
```
combined with the confirmed absence of `nonReentrant`/`ReentrancyGuard` anywhere in `evm/tron/contracts/apps/IntentGatewayV2.sol`. A background Devin session with full repo/tool access should verify the fill-order code path in this file to confirm or rule out a concrete exploit chain before treating this as fully confirmed.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L434-481)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }

        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L216-227)
```text
    /**
     * @dev Same-chain fee theft is now blocked by the CEI fix.
     *
     * Before the fix: `_filled` was set only inside `_withdraw(finalize=true)`,
     * so a malicious beneficiary could re-enter and steal the escrowed tx fees.
     *
     * After the fix: `_filled[commitment] = msg.sender` is set at the top of
     * `_fillSameChain`, before the output loop. The reentrant `fillOrder` call
     * therefore hits `Filled()`, propagates through `receive()`, causes the ETH
     * transfer to return false, and the outer call reverts with
     * `InsufficientNativeToken()` — rolling back all state changes.
     */
```

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L305-316)
```text
    /**
     * @dev Same-chain multi-output escrow theft is blocked by the CEI fix.
     *
     * Before the fix: on a two-output order (ETH + ERC-20), the malicious
     * beneficiary could re-enter during the ETH transfer, self-fill the ERC-20
     * output (net-zero cost), trigger `_withdraw(finalize=true)`, and steal the
     * entire input[1] escrow.
     *
     * After the fix: `_filled[commitment]` is set before the loop, so the
     * reentrant call reverts with `Filled()`. The whole transaction reverts with
     * `InsufficientNativeToken()` and no state is mutated.
     */
```
