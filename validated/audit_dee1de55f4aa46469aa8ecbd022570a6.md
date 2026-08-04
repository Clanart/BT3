## Analysis

The ZkSync report's core broken invariant: **a function that accepts/forwards native ETH along a cross-chain fee-payment path fails to correctly account for the ETH actually consumed vs. supplied**, causing funds to be undeliverable or stuck instead of returned to the rightful party.

Hyperbridge's own `EvmHost.dispatch(...)` and the mainline `evm/src/apps/IntentGatewayV2.sol::placeOrder` implement this pattern correctly: after calling `swapETHForExactTokens{value: msgValue}(order.fees, ...)`, they capture the returned `amounts[0]` (actual ETH spent), decrement `msgValue -= amounts[0]`, and then explicitly refund any leftover `msgValue` back to `msg.sender`.

The **Tron port** of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, duplicates the fee-escrow swap call but drops the refund logic entirely.

### Title
Unrefunded excess native ETH becomes permanently trapped (and later sweepable by protocol admin) in `IntentGatewayV2.placeOrder` fee-swap path - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
In `placeOrder`, when `order.fees > 0` and the caller supplies native ETH (`msgValue > 0`), the contract calls `IUniswapV2Router02.swapETHForExactTokens{value: msgValue}(order.fees, ...)` passing the *entire remaining* `msgValue`, but never reads the function's returned `amounts[0]` (actual ETH spent) and never refunds the difference to the user.

### Finding Description
`UniswapV2Router02.swapETHForExactTokens` only consumes the ETH needed to produce `order.fees` fee tokens and refunds the unused ETH back to its *caller* — here, the `IntentGatewayV2` contract itself (since the call is made with `{value: msgValue}` from the contract, not forwarded from the user directly): [1](#0-0) 

Compare with the mainline EVM version of the same contract, which correctly captures the swap's actual spend and refunds the remainder to `msg.sender`: [2](#0-1) 

The Tron variant omits both the `amounts[0]` capture/`msgValue` decrement and the final "refund any unspent native tokens to the user" block that exists in the mainline contract and in the other native-ETH paths of this same file (e.g. `ExtrinsicIntents.sol` fillOrder logic performs analogous refunds): [3](#0-2) 

As a result, any ETH the Uniswap router refunds to the contract after the exact-output swap is silently absorbed into the contract's balance instead of being returned to the order placer. The contract imports a `SweepDust` type, indicating a governance/admin path exists to later sweep contract-held "dust" balances — meaning ETH that rightfully belongs to the user who overpaid for the fee swap ends up reclassified as protocol dust and can be swept by the admin instead of refunded to the depositor: [4](#0-3) 

This mirrors the ZkSync bug class exactly: a value-forwarding cross-chain/fee-payment call whose actual consumed-vs-supplied ETH accounting is broken, causing native token to become inaccessible to its rightful owner and requiring privileged intervention (in ZkSync's case no `receive()`/refund path existed at all; here the refund logic that exists elsewhere in the same codebase was simply not carried over to this contract).

### Impact Explanation
This is a direct loss-of-funds bug for any user who places an order with `order.fees > 0` and pays with native token (`msg.value`) on the Tron deployment: any ETH sent beyond what the swap actually needs to acquire `order.fees` worth of fee token is not returned. Unlike a normal AMM slippage/dust issue, this happens on every overpaid call, not just an edge case, and the trapped value accumulates in the contract where it is only recoverable by governance's dust-sweep mechanism — not by the user who lost it. This satisfies the bounty's "stealing or loss of funds" and "unauthorized... wrong beneficiary or amount" criteria without requiring any malicious relayer, prover, or admin action — it is triggered purely by a normal unprivileged user calling `placeOrder` with `msg.value` greater than the exact fee-swap cost (which is the common case, since callers cannot know the exact on-chain swap price in advance and must overpay for safety).

### Likelihood Explanation
High likelihood in practice: callers dispatching a fee-based order with native ETH essentially always send some safety margin above the exact quoted amount (since the Uniswap price can move between quote and execution), so the "leftover ETH" condition triggers on essentially every native-fee order placed via this Tron contract, not just an unusual edge case.

### Recommendation
Mirror the mainline `evm/src/apps/IntentGatewayV2.sol` logic in the Tron contract: capture the `amounts` return value from `swapETHForExactTokens`, decrement `msgValue` by the actual amount spent, and refund any remaining `msgValue` to `msg.sender` at the end of `placeOrder` (and audit any other native-ETH swap call sites in the Tron contract for the same omission).

### Proof of Concept
1. Deploy/trace `evm/tron/contracts/apps/IntentGatewayV2.sol` (or point tests at it, analogous to `evm/tests/foundry/IntentGatewayV2Test.sol::testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, which validates the *mainline* contract does refund correctly): [5](#0-4) 
2. Build an `Order` with `order.fees = 1e18` (e.g. 1 DAI-equivalent) and no ERC20-only inputs.
3. Call `placeOrder{value: 5 ether}(order, salt)` on the Tron contract.
4. Observe: the swap only consumes a small fraction of the 5 ETH to acquire the fee token; the Uniswap router refunds the rest to the `IntentGatewayV2` contract's balance (not to the caller).
5. Assert: unlike the mainline contract (where `user.balance` recovers almost all of the 5 ETH), on the Tron contract the caller's balance decreases by the full 5 ETH and the contract's own ETH balance increases by the unspent amount, with no code path to return it to the user.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L21-35)
```text
import {
    PaymentInfo,
    TokenInfo,
    DispatchInfo,
    Order,
    SweepDust,
    Params,
    ParamsUpdate,
    DestinationFee,
    WithdrawalRequest,
    FillOptions,
    SelectOptions,
    CancelOptions,
    NewDeployment
} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-482)
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
```

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L157-168)
```text
        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3460-3499)
```text
    /// @notice placeOrder with fee swap refunds unused ETH after swapETHForExactTokens.
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
