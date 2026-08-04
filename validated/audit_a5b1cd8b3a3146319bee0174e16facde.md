I have enough evidence to confirm this analog. The Tron variant of `IntentGatewayV2.placeOrder` never refunds unspent native ETH to the user, unlike the EVM/mainline version which explicitly performs this refund.

### Title
Unrefunded Excess ETH Permanently Trapped in Tron IntentGatewayV2.placeOrder — Missing msgValue Refund Path - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
In the Tron deployment of `IntentGatewayV2`, `placeOrder()` computes a running `msgValue` counter that is decremented as native-token inputs and the fee-swap consume ETH, exactly like the canonical EVM version. However, the canonical EVM contract (`evm/src/apps/IntentGatewayV2.sol`) ends `placeOrder()` with an explicit refund of any leftover `msgValue` to `msg.sender` [1](#0-0) , while the Tron contract's `placeOrder()` has no equivalent step — the function proceeds straight from the fee-escrow block to `emit OrderPlaced` with no refund of the tracked `msgValue` remainder [2](#0-1) .

### Finding Description
`placeOrder()` tracks `msgValue = msg.value` and subtracts native-input amounts [3](#0-2) , and, when `order.fees > 0`, further subtracts the ETH spent by `swapETHForExactTokens` for the solver-fee token swap [4](#0-3) . In both branches the actual ETH consumed can be strictly less than `msg.value` — a user placing a native-token order or paying `order.fees` in ETH via the Uniswap V2 router will nearly always overpay, since `swapETHForExactTokens` only consumes the ETH needed to buy the exact fee-token amount and returns the rest to the caller (this contract), and native-input amounts are user-supplied estimates.

In the reference EVM contract, this leftover is captured and actively sent back to `msg.sender`: `if (msgValue > 0) { (bool sent,) = msg.sender.call{value: msgValue}(""); ... }` [1](#0-0) . This exact clean-up step is entirely absent from the Tron variant's `placeOrder()` — the last lines of the function only build `OrderPlaced` and return [5](#0-4) .

Any ETH left in `msgValue` after the input/fee accounting therefore stays inside the contract's actual balance but is never recorded as belonging to any order, escrow slot, or user. It cannot be reclaimed by the depositor: `cancelOrder`/`withdraw` only release amounts tracked in `_orders[commitment][token]` [6](#0-5) , and the only path that can move arbitrary native balance out of the contract is the privileged `SweepDust` action gated behind Hyperbridge governance (`onAccept`, requiring `incoming.request.source == hyperbridge`) [7](#0-6) . This mirrors the external report's root cause precisely: the full `msg.value` is implicitly retained/forwarded instead of only the amount actually used, and the excess is not returned to its rightful owner.

### Impact Explanation
Every unprivileged user who overpays native ETH when placing a native-input order, or pays the solver fee in ETH (the swap virtually never consumes exactly `msgValue`), permanently loses the difference. This is a direct, unauthorized loss of user funds triggered purely through the normal, documented `placeOrder{value: ...}` entrypoint — no malicious peer, relayer, or admin action is required. The funds become effectively locked in the contract, recoverable only via a privileged, cross-chain governance-triggered `SweepDust` action, meaning ordinary users have no self-service recovery path.

### Likelihood Explanation
High. This triggers on the ordinary, documented usage pattern of `placeOrder`, where callers commonly send `nativeValue`/`msg.value` in excess of the exact required amount (the mainline EVM/documentation explicitly acknowledges this pattern and refunds it, per `docs/content/developers/evm/intent-gateway/placing-orders.mdx:277` describing that "unused native is refunded"). Any Tron caller following the same documented flow — sending headroom ETH for the fee-token swap or a slightly generous native input amount — silently loses the surplus every single time, with no error or revert to warn them.

### Recommendation
Add the same trailing refund block used in `evm/src/apps/IntentGatewayV2.sol` to the Tron contract's `placeOrder()`: after the fee-escrow logic, refund any remaining `msgValue` to `msg.sender` via a low-level call, reverting with `InsufficientNativeToken()` on failure, mirroring lines 364-368 of the canonical contract.

### Proof of Concept
1. On Tron, deploy/target `IntentGatewayV2` with `order.fees > 0` and native `_params.uniswapV2Router`/`feeToken` configured.
2. Call `placeOrder{value: X}(order, graffiti)` where `X` comfortably exceeds the ETH required both for native `order.inputs` and for `swapETHForExactTokens` to acquire `order.fees` fee tokens (e.g., `X = 5 ether` vs. an actual cost of `0.01 ether`, matching the exact overpayment scenario already exercised for the mainline contract in `testPlaceOrder_FeeSwap_RefundsExcessNativeToken` [8](#0-7) ).
3. Observe that `IUniswapV2Router02.swapETHForExactTokens` only spends the ETH needed to acquire `order.fees` tokens, refunding the rest to the Tron `IntentGatewayV2` contract itself (not the user), and the function exits without forwarding any of it back to `msg.sender` [2](#0-1) .
4. Confirm the user's ETH balance decreased by the full `X`, not by the actual amount consumed, and that the difference is stuck in the `IntentGatewayV2` contract's balance with no order/commitment tracking it, unlike the EVM version's equivalent test `testPlaceOrder_FeeSwap_RefundsExcessNativeToken`, which asserts the user is refunded.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L364-368)
```text
        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-674)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;

            // Update destination-specific protocol fees if provided
            for (uint256 i; i < update.destinationFees.length;) {
                bytes32 stateMachineId = update.destinationFees[i].stateMachineId;
                uint256 feeBps = update.destinationFees[i].destinationFeeBps;
                _destinationProtocolFees[stateMachineId] = feeBps;

                unchecked {
                    ++i;
                }
                emit DestinationProtocolFeeUpdated(stateMachineId, feeBps);
            }
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }
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
