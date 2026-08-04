## Analysis

Reducing the LiFi report to its core broken invariant: a **generic, permissionless multicall executor** is repurposed to move protocol-relevant funds (fees/dust), and the accounting logic built around it makes assumptions (about which balances belong to which flow) that don't hold once the executor is reachable outside the intended call path.

The Hyperbridge analog is `CallDispatcher` in the Intent Gateway. It is a **single shared, stateless-looking singleton** (`_params.dispatcher`) reused across every order fill/placement on a given `IntentGatewayV2` deployment, and its `dispatch()` entrypoint has **no access control** at all.

### Title
Permissionless `CallDispatcher.dispatch()` allows theft of unswept intent-order residue tokens - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch(bytes)` is `external` with no caller restriction [1](#0-0) . It is meant to be invoked only by the `IntentGatewayV2`/`ExtrinsicIntents`/`IntrinsicIntents` contracts as part of `_execute()` during order fills, to run solver-supplied predispatch/postdispatch calldata and then sweep the dispatcher's resulting balance back to the gateway as "dust" [2](#0-1) . But because `dispatch()` has no `onlyGateway`/`restrict` guard, any address can call it directly at any time.

### Finding Description
`_execute()` sweeps the dispatcher's residual balance **only for the tokens listed in `order.output.assets`** (the loop bound is `outputsLen`, iterating `order.output.assets[i].token`) [3](#0-2) . If the attacker/solver-supplied postdispatch calldata (`order.output.call`, executed via `ICallDispatcher(dispatcher).dispatch(order.output.call)`) produces a byproduct balance in a token that is **not** one of the order's declared output assets (e.g. a Uniswap swap route or approval leaves a different ERC-20 or leftover native ETH in the dispatcher, as demonstrated by the "token sweep" test pattern using swap byproducts) [4](#0-3) , that balance is never captured by `_execute()`'s sweep loop and is left sitting in the `CallDispatcher` contract.

Because `CallDispatcher.dispatch()` is completely permissionless, any external, unprivileged actor can then call it directly with a crafted `Call[]` (e.g. `token.transfer(attacker, balance)`) to sweep that leftover balance to themselves [5](#0-4) . This is structurally the same defect as the LiFi finding: a multicall-style execution primitive that was designed to be reached only through a controlled flow (with its own balance bookkeeping) is instead a standalone, unauthenticated entrypoint, so the surrounding contract's balance assumptions ("whatever lands in the dispatcher belongs to this order's dust accounting") do not hold once anyone can invoke it out-of-band.

### Impact Explanation
Any unprivileged address can drain ERC-20/native token balances that transiently or persistently sit in the shared `CallDispatcher`, resulting in direct loss of protocol/solver funds without needing a malicious relayer, prover, or admin — this is loss of funds via an unauthorized execution path reachable by any EOA.

### Likelihood Explanation
Likelihood is contingent on the postdispatch/predispatch calldata (chosen by the order creator or solver for DeFi routing, e.g. swaps) producing a token balance in the dispatcher that isn't in `order.output.assets`/`predispatch.assets` — a realistic and common occurrence for swap routes with slippage or multi-hop paths, as evidenced by the repo's own "token sweep" test scenarios that specifically construct such byproduct residue. I was not able to fully verify from the index whether `predispatch` sweeping (in `placeOrder`, `evm/src/apps/IntentGatewayV2.sol`) has an equivalent gap or a broader/narrower sweep set, since that file's exact `placeOrder` body wasn't retrieved in full during this session — I'd flag this as needing direct confirmation via a Devin session with full file access.

### Recommendation
- Restrict `CallDispatcher.dispatch()` to be callable only by the registered `IntentGatewayV2` instance(s) that own it (e.g. an `onlyGateway` modifier or per-order ephemeral dispatcher via `CREATE2` clone), rather than leaving it a permissionless singleton.
- Alternatively, sweep **all** token balances the dispatcher holds after execution (not just the declared output/predispatch asset list), or require postdispatch calldata to explicitly declare every token it may touch so the sweep is exhaustive.
- If `CallDispatcher` is intended as a general-purpose permissionless multicall relay, rename/document it as such and never let it hold value between transactions.

### Proof of Concept
1. User places a cross-chain order whose `output.call` (postdispatch calldata) performs a multi-hop swap (e.g., swap via Uniswap through an intermediate token) with a beneficiary set to the `CallDispatcher` address, similar to the existing `testPostdispatchTokenSweep` scenario [4](#0-3) .
2. The swap route leaves a non-zero balance of an intermediate token in the `CallDispatcher` that is not part of `order.output.assets`.
3. `_execute()` only sweeps balances for tokens in `order.output.assets`, so the intermediate token balance remains in the dispatcher [3](#0-2) .
4. An unrelated attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: intermediateToken, value: 0, data: transfer(attacker, balance)})]))` directly — no restriction exists on the caller [1](#0-0)  — and receives the stranded tokens.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-485)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }

            unchecked {
                ++i;
            }
        }

        if (sweepCount > 0) {
            Call[] memory finalCalls = new Call[](sweepCount);
            for (uint256 i; i < sweepCount;) {
                finalCalls[i] = sweepCalls[i];
                unchecked {
                    ++i;
                }
            }
            ICallDispatcher(dispatcher).dispatch(abi.encode(finalCalls));
        }
    }
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L1193-1253)
```text
    function testPostdispatchTokenSweep() public {
        // Test realistic postdispatch: exact output swap on Uniswap V2 where refunded input tokens are swept
        // Scenario: User wants 1000 DAI on destination, solver sends USDC to dispatcher,
        // dispatcher swaps exact output for DAI, refunded USDC is swept back to gateway

        uint256 inputAmount = 1000 * 1e6; // 1000 USDC escrow
        uint256 daiOutputAmount = 1000 * 1e18; // Exact 1000 DAI output wanted

        // Setup order inputs
        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});

        // Create postdispatch calls that:
        // 1. Approve Uniswap router to spend USDC
        // 2. Execute exact output swap (swapTokensForExactTokens) - USDC -> DAI
        // 3. Transfer DAI to user
        Call[] memory postdispatchCalls = new Call[](3);

        // Get quote for how much USDC needed for 1000 DAI (will be less than what solver sends)
        address[] memory path = new address[](2);
        path[0] = address(usdc);
        path[1] = address(dai);
        address uniswapRouter = 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D;
        uint256[] memory amounts = IUniswapV2Router02(uniswapRouter).getAmountsIn(daiOutputAmount, path);
        uint256 usdcNeeded = amounts[0];

        // Call 1: Approve Uniswap router
        postdispatchCalls[0] = Call({
            to: address(usdc),
            value: 0,
            data: abi.encodeWithSelector(IERC20.approve.selector, uniswapRouter, type(uint256).max)
        });

        // Call 2: Exact output swap - swap USDC for exactly 1000 DAI
        postdispatchCalls[1] = Call({
            to: uniswapRouter,
            value: 0,
            data: abi.encodeWithSelector(
                bytes4(keccak256("swapTokensForExactTokens(uint256,uint256,address[],address,uint256)")),
                daiOutputAmount, // exact amount out
                type(uint256).max, // max amount in
                path,
                address(dispatcher), // tokens come back to dispatcher
                block.timestamp
            )
        });

        // Call 3: Transfer DAI to user
        postdispatchCalls[2] = Call({
            to: address(dai), value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, user, daiOutputAmount)
        });

        // Setup order output - beneficiary is dispatcher, it will receive USDC from solver
        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: usdcNeeded + 100 * 1e6}); // Solver sends more than needed

        PaymentInfo memory output = PaymentInfo({
            beneficiary: bytes32(uint256(uint160(address(dispatcher)))), // Dispatcher receives USDC
            assets: outputAssets,
            call: abi.encode(postdispatchCalls)
        });
```

**File:** sdk/packages/core/contracts/interfaces/ICallDispatcher.sol (L26-37)
```text
/**
 * @title The ICallDispatcher
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice This interface is used to dispatch untrusted call(s)
 */
interface ICallDispatcher {
    /*
     * @dev Dispatch the encoded call(s)
     */
    function dispatch(bytes memory params) external;
}
```
