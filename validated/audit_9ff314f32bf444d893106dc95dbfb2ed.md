### Title
Unrefunded native-token overpayment permanently traps user funds in `IntentGatewayV2.placeOrder()` — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2.placeOrder()` performs a native-ETH → fee-token swap using the router's `swapETHForExactTokens` but never accounts for the change the router returns, and never refunds any leftover `msgValue` to the caller at the end of the function. This is the same broken invariant as the M-33 report: proceeds from an operation that produces excess value are left stranded on the contract instead of being returned to the depositing user, and the excess is not tracked by any escrow/dust accounting.

### Finding Description
In the canonical EVM `IntentGatewayV2.sol`, the fee-swap block correctly tracks the swap's actual cost and refunds anything unspent: [1](#0-0) 

Specifically it does `msgValue -= amounts[0]` after the swap, and then unconditionally does `if (msgValue > 0) { msg.sender.call{value: msgValue}(""); }` before returning.

The Tron fork of the same contract implements the identical fee-swap logic but drops both safeguards: [2](#0-1) 

Here, `IUniswapV2Router02.swapETHForExactTokens{value: msgValue}(order.fees, ...)` is called with the router `amountInMax = msgValue` (the *entire* remaining native value after input escrow), while the exact amount needed is `order.fees` worth of fee token. Uniswap V2's `swapETHForExactTokens` refunds unused ETH to `msg.sender` — which here is the `IntentGatewayV2` contract itself, not the end user — via the router's internal `TransferHelper.safeTransferETH(msg.sender, msg.value - amounts[0])`. After this call:
- `msgValue` is never decremented by the actual amount spent (unlike the EVM version's `amounts[0]` capture).
- The function immediately proceeds to emit `OrderPlaced` and returns; there is no final `if (msgValue > 0) { ... refund ... }` block at all.

The refunded ETH change from the swap therefore lands and stays on the `IntentGatewayV2` contract's own balance. It is not credited to `_orders[commitment][...]` escrow, not emitted as `DustCollected`, and not otherwise attributable to the user who paid it. This mirrors exactly the M-33 root cause: "all sale/swap proceedings now stay with the contract, but remainder handling logic isn't present, so leftover amounts... are lost for user and left on the contract balance," unaccounted by system bookkeeping.

### Impact Explanation
Any user calling `placeOrder` with `order.fees > 0` and paying with native token (a normal, documented flow — see `docs/content/developers/evm/intent-gateway/placing-orders.mdx` describing "unused native is refunded" as the expected behavior) will have their unspent native ETH permanently locked in the gateway contract on Tron instead of returned. This is a direct, unconditional loss of user funds on every fee-swap overpayment — not a rare edge case, since callers must send `msg.value` that at least covers the worst-case swap price, and any slippage margin becomes unrecoverable dust that is not tracked by the escrow/dust-sweep bookkeeping (`_orders`, `DustCollected`), unlike every other surplus/overpayment path in this same codebase (`IntrinsicIntents`, `ExtrinsicIntents`, the canonical EVM `IntentGatewayV2.sol`), all of which explicitly refund unspent `msgValue` to `msg.sender`. This satisfies the bounty's "stealing or loss of funds" impact category — funds move exactly once, but to the wrong beneficiary (the contract) instead of the rightful one (the user).

### Likelihood Explanation
High. This is not a privileged-actor or malicious-peer scenario — it triggers on the unprivileged, public `placeOrder()` entrypoint under normal usage whenever a user pays the solver fee in native token with any safety margin above the exact required swap cost (which is the SDK-recommended pattern, since exact swap cost cannot be known in advance). No proof forgery, relayer collusion, or governance action is required.

### Recommendation
Mirror the canonical EVM implementation in the Tron contract:
1. Capture `amounts[0]` (the actual ETH spent) from `swapETHForExactTokens`, and decrement `msgValue -= amounts[0]`.
2. After the fee block, add the same closing refund: `if (msgValue > 0) { (bool sent,) = msg.sender.call{value: msgValue}(""); if (!sent) revert InsufficientNativeToken(); }`.

### Proof of Concept
1. User calls `IntentGatewayV2(tron).placeOrder{value: X}(order, graffiti)` where `order.fees = F` (in fee-token units) and inputs are ERC20 (no native input consumption), so all of `X` is available for the fee swap.
2. Inside `placeOrder`, the fee block executes `swapETHForExactTokens{value: X}(F, path, address(this), deadline)`. Suppose the actual ETH cost to acquire `F` fee-token is `c < X`.
3. The Uniswap V2 router refunds `X - c` ETH to `address(this)` (the gateway contract), per standard V2 router behavior.
4. `placeOrder` returns without ever touching `msgValue` again — the refunded `X - c` ETH remains on the gateway's balance, uncredited to any order, unemitted as dust, and unreachable by the user who paid it.
5. Compare with `evm/src/apps/IntentGatewayV2.sol`'s `testPlaceOrder_FeeSwap_RefundsExcessNativeToken` test [3](#0-2)  which asserts the EVM version *does* refund; running the equivalent scenario against the Tron contract would show the user's ETH balance short by the full `X` sent rather than only `c` spent.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-368)
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

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-497)
```text
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

        emit OrderPlaced({
            user: order.user,
            source: order.source,
            destination: order.destination,
            deadline: order.deadline,
            nonce: order.nonce,
            fees: order.fees,
            session: order.session,
            predispatch: order.predispatch.assets,
            inputs: reducedInputs,
            beneficiary: order.output.beneficiary,
            outputs: order.output.assets
        });
    }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3461-3499)
```text
    function testPlaceOrder_FeeSwap_RefundsExcessNativeToken() public {
        uint256 inputAmount = 1000 * 1e6;
        uint256 feeAmount = 1 * 1e18; // 1 DAI worth of fees

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 1000,
            nonce: 0,
            fees: feeAmount,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        uint256 userEthBefore = user.balance;

        vm.startPrank(user);
        usdc.approve(address(intentGateway), inputAmount);
        // Send 5 ETH for a fee swap that should cost much less
        intentGateway.placeOrder{value: 5 ether}(order, bytes32(0));
        vm.stopPrank();

        // User should get back most of the 5 ETH — the swap only needed a tiny fraction
        uint256 ethSpent = userEthBefore - user.balance;
        assertTrue(ethSpent < 1 ether, "User should have been refunded most of the 5 ETH");
        assertTrue(ethSpent > 0, "User should have spent some ETH on the fee swap");
    }
```
