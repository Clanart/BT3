## Analysis

The reported bug pattern — a payable function accepting native coin for a fee/payment leg, failing to precisely account for consumed vs. unspent `msg.value`, and never returning the unspent amount to the caller — has a direct local analog in `evm/tron/contracts/apps/IntentGatewayV2.sol`.

### Title
Unrefunded excess native ETH permanently trapped in `placeOrder()` fee-swap path - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder()` on the Tron deployment swaps native ETH for the protocol fee token via `swapETHForExactTokens{value: msgValue}(...)` but, unlike the canonical EVM implementation, never captures the router's returned "spent" amount, never decrements the local `msgValue` tracker, and never refunds the leftover balance to the user. Any native coin sent above the exact fee-swap cost is transferred into the router call, and — depending on router refund semantics — either becomes stranded in the `IntentGatewayV2` contract itself (since the contract, not the original user, is `msg.sender` from the router's perspective) rather than returned to the order placer.

### Finding Description
In the canonical EVM `IntentGatewayV2.sol`, the fee-swap block correctly tracks the swap cost and refunds the remainder: [1](#0-0) 

The Tron variant of the same function omits this accounting entirely — it does not capture `amounts[0]` from `swapETHForExactTokens`, does not decrement `msgValue`, and has no trailing refund block at all: [2](#0-1) 

Because Uniswap V2's `swapETHForExactTokens` refunds unspent ETH to `msg.sender` of that call — which, in this nested call, is the `IntentGatewayV2` contract itself, not the original order-placer — any native token sent beyond the exact fee cost is absorbed into the gateway contract's balance with no code path that returns it to the user. The user-facing docs explicitly promise this refund behavior ("unused native is refunded") for the fee-payment flow: [3](#0-2) 

The only mechanism capable of moving stranded native ETH out of the contract is `SweepDust`, which is exclusively reachable through an authenticated cross-chain `onAccept` message originating from Hyperbridge itself (governance-gated), not something the depositing user can invoke: [4](#0-3) 

This mirrors the exact broken invariant from the external report: a payable entry point accepts native coin for a fee-denominated payment, an intermediate variable/flow fails to reconcile the actual consumed amount against `msg.value`, and the difference is neither reverted-on-mismatch nor refunded — resulting in silent loss of user-supplied native coin.

### Impact Explanation
Any user calling `placeOrder()` with `order.fees > 0` and paying the fee in native token (as instructed by the SDK/docs, which recommend sending `nativeValue` plus a buffer since exact swap cost is only known post-execution) will have 100% of any overpayment permanently locked in the `IntentGatewayV2` contract, unrecoverable by the user. This is a direct, unconditional loss of user funds triggered by the normal, documented usage pattern — not requiring any malicious relayer, prover, or admin.

### Likelihood Explanation
High. Because the exact on-chain price used by `swapETHForExactTokens` can differ from the off-chain quote at call time, users are instructed to send `nativeValue` (with slack) rather than an exact amount, meaning overpayment is an expected/common condition rather than an edge case. Every native-fee-paying `placeOrder()` call on the Tron deployment is exposed.

### Recommendation
Mirror the EVM implementation: capture the `amounts` array returned by `swapETHForExactTokens`, decrement the local `msgValue` by the actual amount spent, and add a trailing native-refund block (`(bool sent,) = msg.sender.call{value: msgValue}(""); if (!sent) revert InsufficientNativeToken();`) at the end of `placeOrder()`, matching the pattern already present in `evm/src/apps/IntentGatewayV2.sol`.

### Proof of Concept
1. User calls `intentGateway.placeOrder{value: X}(order, graffiti)` on the Tron deployment where `order.fees > 0` is to be paid in native token, and `X` intentionally or unavoidably exceeds the actual swap cost required to acquire `order.fees` worth of the fee token.
2. Execution reaches: [5](#0-4) 
3. `swapETHForExactTokens{value: msgValue}` executes; the router refunds unspent ETH to its caller, which is the `IntentGatewayV2` contract (not the original user), since this call is made from inside the contract.
4. Function proceeds to `emit OrderPlaced(...)` and returns without any refund path.
5. Result: `address(intentGateway).balance` permanently increases by the overpaid amount, and the user has no self-service way to reclaim it — only a Hyperbridge-governance-triggered `SweepDust` message can move it, and even then to a beneficiary chosen by that governance action, not automatically the original depositor.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-674)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }

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

**File:** docs/content/developers/evm/intent-gateway/placing-orders.mdx (L275-284)
```text
#### Native token

The placement transaction carries `nativeValue` extra wei, which the gateway swaps into the fee token through its configured router (unused native is refunded). Check the balance now; the placement step adds `nativeValue` to the transaction:

```typescript title="check-native-fee.ts" lineNumbers
const nativeBalance = await sourceChain.client.getBalance({ address: account.address })
if (nativeBalance < nativeValue) throw new Error("Insufficient native balance for the solver fee")
```

The wallet still pays normal transaction gas on top of this value.
```
