### Title
Sentinel-key collision lets a user fabricate unbacked escrow that drains real `feeToken` on order fill - (File: evm/src/apps/IntentGatewayV2.sol, evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`IntentGatewayV2`/`IntentsBase` track both per-token escrow and the order's relayer/fill fee in the *same* mapping, `_orders[commitment][token]`, using a reserved constant `TRANSACTION_FEES` as the mapping key for the fee amount. Nothing in `placeOrder()` rejects a user-supplied input token whose address equals the `TRANSACTION_FEES` sentinel. Because that sentinel is not a real ERC20 contract, `safeTransferFrom`/low-level `.call` to it during escrow crediting trivially "succeeds" (a call to an address with no code always returns success with empty return data), so the escrow-credit loop writes an attacker-chosen amount into `_orders[commitment][TRANSACTION_FEES]` without any real token ever being pulled from the user. `withdraw()` later reads that exact slot as the order's fee and pays it out in the real `feeToken()` to the solver/beneficiary — a real-value payout backed by a fabricated (uncollateralized) escrow entry.

### Finding Description
`placeOrder()` credits escrow per input token: [1](#0-0) 

and separately escrows the fee under the fixed key `TRANSACTION_FEES`: [2](#0-1) 

The Tron/EVM sibling implementation shows the same two writes into one mapping, `_orders[commitment][token]` for real inputs and `_orders[commitment][TRANSACTION_FEES]` for the fee, with only a same-token duplicate check (`_orders[commitment][token] != 0`) — nothing that special-cases or rejects `token == TRANSACTION_FEES`: [3](#0-2) 

At settlement, `withdraw()` treats whatever value sits in `_orders[commitment][TRANSACTION_FEES]` as legitimate accrued fees and pays it out in the protocol's real fee token: [4](#0-3) 

This mirrors the root cause of the seed report exactly: a token-handling code path is written to assume one narrow case (real ERC20 inputs distinct from the internal bookkeeping sentinel) and never validates or special-cases the alternate case (a user-supplied token address that collides with a protocol-internal sentinel key). Just as `_tokenToPairedLpToken()` silently mishandled the podded-fTKN case because `IS_PAIRED_LENDING_PAIR` only branches on one variant, `placeOrder()` silently mishandles the `token == TRANSACTION_FEES` case because the "reject duplicate input tokens" guard only checks token-vs-token collisions, not token-vs-sentinel collisions — and a call to a token address with no deployed code does not revert, so the transfer step that is supposed to enforce real collateralization is bypassed entirely.

### Impact Explanation
An unprivileged user placing an order can set one of `order.inputs[i].token` to the `TRANSACTION_FEES` constant address. The escrow-credit step for that "token" succeeds without moving any real value (no contract exists at that address to actually debit the user), yet `_orders[commitment][TRANSACTION_FEES]` is populated with an attacker-chosen amount. When the order is later filled and settled, `withdraw()` reads that slot as the order's fee and transfers real `feeToken()` balance held by the gateway to the solver/beneficiary — funds that were never actually deposited by the order creator. This is a direct "stealing or loss of funds" / logic-attack primitive: the gateway's real feeToken reserves (funded by other users' legitimately escrowed fees) can be drained to an attacker-controlled beneficiary with no genuine backing, reachable by any user through the public `placeOrder`/fill flow with no relayer, prover, or admin compromise required.

### Likelihood Explanation
The path uses only public, permissionless entry points (`placeOrder` then a normal `fillOrder`/settlement), requires no privileged role, and depends only on knowing the constant `TRANSACTION_FEES` sentinel value (a `constant`, visible on-chain/in ABI) and constructing an `Order.inputs` entry with that address. The only mitigating factor is that the actual quantity drained is bounded by the gateway's real `feeToken` balance at settlement time, and exploitation requires the `withdraw()`/fee-payout code path to actually run for a commitment whose `TRANSACTION_FEES` slot was poisoned this way — both of which are attacker-controllable in the normal order lifecycle.

### Recommendation
In `placeOrder()` (and any sibling implementation: `IntentGatewayV2.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`), explicitly reject any `order.inputs[i].token == TRANSACTION_FEES` (and any other internal sentinel constants used as mapping keys) before crediting escrow, e.g. `if (token == TRANSACTION_FEES) revert InvalidInput();`. Additionally, do not rely on a bare low-level `.call`/`safeTransferFrom` success as proof of real value transfer for arbitrary attacker-supplied addresses — verify the target has code (e.g. `token.code.length > 0`) or, more robustly, track fee escrow in a variable/struct field separate from the generic `_orders[commitment][token]` mapping so no user-controlled token address can ever alias the fee-accounting key.

### Proof of Concept
1. Attacker calls `placeOrder()` with `order.inputs = [{ token: TRANSACTION_FEES, amount: X }]` and `order.fees = 0` (so the later `if (order.fees > 0)` block never overwrites the slot).
2. During the escrow-credit loop, `IERC20(TRANSACTION_FEES).safeTransferFrom(msg.sender, address(this), X)` is called against an address with no deployed contract code; the EVM returns success trivially, no tokens move, and `_orders[commitment][TRANSACTION_FEES] += X` is recorded. [5](#0-4) 
3. The order is filled through the normal flow; settlement calls `withdraw()`, which reads `fees = _orders[commitment][TRANSACTION_FEES]` (== X) and transfers `X` of the real `feeToken()` to `beneficiary`. [6](#0-5) 
4. The beneficiary/solver receives `X` real fee-token units that were never deposited by any user — an unbacked payout drawn from the gateway's aggregate fee-token balance.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L448-482)
```text
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
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L676-720)
```text
    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
```
