### Title
Same-chain order fill fees (`order.fees`) intended for cross-chain relayers are diverted to solvers instead of being refunded — `IntrinsicIntents.sol` / `IntentGatewayV2.sol`

### Summary
`order.fees` ("Fill fee") is collected from the user at `placeOrder()` time regardless of whether the order is same-chain or cross-chain, and stored under the `TRANSACTION_FEES` slot for the order's commitment. This fee is meant to compensate the Hyperbridge relayer who submits the `RedeemEscrow`/`RefundEscrow` ISMP message on settlement. For same-chain orders, settlement is local and atomic — there is no ISMP dispatch and therefore no relayer to pay — yet the same-chain fill path (`_fillSameChain` in `IntrinsicIntents.sol`) still calls the shared `_withdraw`/`withdraw` routine with the solver as beneficiary, which unconditionally releases the stored `TRANSACTION_FEES` to that beneficiary. The user's fee payment therefore ends up paid to the solver as a pure windfall instead of being refunded to the user or otherwise routed correctly, mirroring the "fee calculated/deducted but never delivered to the correct party" defect class from the external report.

### Finding Description
In `evm/src/apps/IntentGatewayV2.sol`, `placeOrder()` unconditionally collects `order.fees` from the user and records it for the order commitment: [1](#0-0) 

This happens before any same-chain/cross-chain branching — the fee is collected the same way no matter which fill path will later be used.

For same-chain orders, `IntrinsicIntents._fillSameChain` fills the order and then calls the shared withdrawal routine using the *solver* (`msg.sender`) as beneficiary, with no cross-chain dispatch at all: [2](#0-1) 

The withdrawal routine (`withdraw`, shown here in the maintained Tron mirror, which shares the same `IntentsBase`/`_withdraw` design used by the EVM implementation) releases escrowed inputs to the beneficiary and then unconditionally transfers any stored `TRANSACTION_FEES` to that same beneficiary — with no check for whether a relayer actually delivered anything: [3](#0-2) 

Because `_fillSameChain` never performs any cross-chain dispatch, there is no relayer to compensate, yet the fee is paid to the solver rather than refunded to the user or swept to the protocol. This is functionally identical to the ElytraDepositPoolV1 issue: a fee is computed and taken from the user, but the invariant "this fee reaches its intended, designated recipient" is broken — here the intended recipient (a relayer) never receives it, and an unrelated party (the solver) captures it instead.

### Impact Explanation
This is direct, unauthorized value transfer away from the user: any `order.fees` amount paid for a same-chain order is siphoned to the solver who fills it, with no corresponding service rendered (no relayer submission occurs for same-chain settlement). A user (or a client/SDK that populates `order.fees` by default, following cross-chain conventions) loses that entire fee on every same-chain order. This falls under "stealing or loss of funds" / "wrong beneficiary" per the bounty's impact gate — no malicious relayer, prover, or governance actor is required; a normal solver benefits automatically and unconditionally from this misrouting.

### Likelihood Explanation
Likelihood is Medium-to-High: `order.fees` is a first-class, user-settable field on every order (same-chain and cross-chain use the identical `Order` struct and `placeOrder` code path), and nothing in `placeOrder` or `_fillSameChain` prevents or zeroes it for same-chain orders. Any user or integration that sets a non-zero fill fee on a same-chain order (e.g., by reusing cross-chain order-construction logic, or a solver deliberately crafting scenarios that induce users to include one) triggers the misrouting on the very next fill, with no special conditions or privileged actors needed.

### Recommendation
Either (a) disallow/zero `order.fees` for same-chain orders in `placeOrder` (revert if `order.fees > 0` when `source == destination`), or (b) have `_fillSameChain` refund the stored `TRANSACTION_FEES` back to the user instead of forwarding it to the solver, since no relayer service is being paid for in the same-chain path. Withdrawal logic should distinguish "relayer-compensation" fee release (only applicable to cross-chain `RedeemEscrow`/`RefundEscrow` settlement) from same-chain fills where the beneficiary of escrow release and the beneficiary of relayer fees should never be conflated.

### Proof of Concept
1. User calls `placeOrder` with `order.source == order.destination` (same-chain order) and `order.fees = X > 0` in the fee token; `X` is transferred from the user and stored as `_orders[commitment][TRANSACTION_FEES] = X` (evm/src/apps/IntentGatewayV2.sol:345-362).
2. A solver calls `fillOrder`, which routes to `IntrinsicIntents._fillSameChain` (no ISMP dispatch occurs — settlement is atomic/local).
3. `_fillSameChain` calls `_withdraw` with `beneficiary = msg.sender` (the solver) (evm/src/apps/intentsv2/IntrinsicIntents.sol:131-134).
4. The withdrawal routine releases the escrowed input tokens *and* the stored `TRANSACTION_FEES` (`X`) to the solver (pattern shown in evm/tron/contracts/apps/IntentGatewayV2.sol:707-714).
5. Net effect: the user paid `X` in fill fees that were never used to pay any relayer (none was needed) and were never refunded — the solver receives `X` for free on top of the normal fill proceeds.

### Citations

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L126-138)
```text
        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
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
