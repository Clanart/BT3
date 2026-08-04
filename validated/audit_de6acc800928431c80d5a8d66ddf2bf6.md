## Title
Permissionless `CallDispatcher.dispatch()` allows anyone to steal residual funds temporarily custodied by the shared call-execution proxy - (File: `evm/src/utils/CallDispatcher.sol`)

## Summary
The Quantstamp report's core defect is that a component meant to execute privileged, protocol-internal follow-up calls (`executeFromPluginExternal`) has no restriction on who may invoke it or what it may target, letting an unprivileged actor reach functions/state that were only supposed to be reachable through a controlled internal flow. Hyperbridge's `CallDispatcher` contract exhibits the same broken invariant in a fund-custody context: it is a single shared, unauthenticated proxy (`dispatch(bytes memory encoded) external`, no `onlyHost`, no `onlyOwner`, no caller allow-list) that multiple privileged flows (`HyperFungibleToken.onAccept`, `WrappedHyperFungibleToken.onAccept`, `IntentsBase._execute`) route real tokens/ETH through as `to` before sweeping residuals back. [1](#0-0) 

## Finding Description
`CallDispatcher.dispatch()` accepts an arbitrary ABI-encoded `Call[]` and executes every call `to.call{value: call.value}(call.data)` with no check on `msg.sender`: [2](#0-1) 

This contract is intentionally deployed once and reused as a shared execution sink by several ISMP apps:
- `WrappedHyperFungibleToken.onAccept()` unlocks/unwraps tokens to a beneficiary that can be set to the `CallDispatcher` address, then invokes `ICallDispatcher(_dispatcher).dispatch(message.data)` to run arbitrary post-transfer calldata using the tokens now sitting on the dispatcher: [3](#0-2) 
- `IntentsBase._execute()` mints/unlocks order outputs to the dispatcher, runs `order.output.call` through it, and then sweeps back only the tokens explicitly listed in `order.output.assets[0..outputsLen)`: [4](#0-3) 

Because the dispatcher executes calls "in its own context" and the sweep logic only tracks tokens it was told about ahead of time, any ERC20/ETH balance that ends up on the `CallDispatcher` contract that is *not* one of the enumerated `outputsLen` assets (e.g. produced by attacker-crafted `order.output.call`/`message.data` performing a swap into an unlisted token, or any leftover approval dust) is left sitting on that contract's balance after the transaction completes. Since `dispatch()` has zero access control, **any address** — not just the app that deposited the funds — can subsequently call `CallDispatcher.dispatch()` directly with a `Call` that transfers that residual balance to themselves. The contract has no notion of "owner" of a given balance, no reentrancy guard, and no restriction tying execution back to the app/order that funded it.

This mirrors the report's exact broken invariant: a component built to allow *only privileged callers* (the app's own internal follow-up logic) to reach fund-moving execution is instead an open door that any party can invoke, because the guard was never placed on the shared execution primitive itself — only on the higher-level flows that are supposed to be the sole callers.

## Impact Explanation
Any token/ETH balance left on the `CallDispatcher` — whether from `IntentGatewayV2`/`IntentsBase` order fills whose composable calldata produces an unswept token, or from `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata execution — is directly stealable by an unrelated third party who simply calls `dispatch()` with a `Call` targeting that token/ETH. This is a direct "stealing or loss of funds" impact against a production bridge contract, matching the bounty's accepted impact class, and requires no relayer, prover, or admin compromise — only observation of a public balance and a single unauthenticated transaction.

## Likelihood Explanation
Likelihood is tied to how often non-enumerated residual balances accumulate on the shared dispatcher. `IntentsBase._execute()` explicitly acknowledges dust accumulation is expected ("solvers can route through DEXes, lending protocols... any residual token balances left on the dispatcher are swept back... and accounted for as protocol dust"), meaning the codebase already expects that arbitrary composable calldata can leave assets on the dispatcher that aren't the pre-declared output tokens. [5](#0-4)  A solver (an unprivileged, permissionless actor who supplies `order.output.call`) fully controls what that calldata does and can trivially cause an unlisted token to be produced and left on the dispatcher, then immediately front-run/self-claim it via a direct `dispatch()` call before any legitimate sweep captures it — no privileged position is required.

## Recommendation
- Restrict `CallDispatcher.dispatch()` to a caller allow-list (e.g., only the registered `IntentGatewayV2`/`HyperFungibleToken`/`WrappedHyperFungibleToken` contracts) rather than leaving it fully public.
- Alternatively/additionally, deploy per-app or per-order ephemeral dispatcher instances (e.g., minimal proxies) instead of one long-lived shared singleton, so no cross-app or cross-order dust can ever accumulate and be reachable by unrelated actors.
- Ensure the dust-sweep logic in `IntentsBase._execute()` enumerates the dispatcher's actual token balances touched by arbitrary calldata (not just the pre-declared `outputsLen` assets), or reject orders whose `output.call` can produce unaccounted asset flows.

## Proof of Concept
1. A solver fills an order via `IntentGatewayV2`/`IntentsBase.fillOrder`, supplying `order.output.call` crafted to route through a DEX and swap part of the output into a token `X` that is **not** listed in `order.output.assets`.
2. `_execute()` dispatches the call via `ICallDispatcher(dispatcher).dispatch(order.output.call)`; token `X` balance is left on the `CallDispatcher` contract because the subsequent sweep loop only iterates `order.output.assets[0..outputsLen)`. [6](#0-5) 
3. Anyone (including the same solver, or a bystander watching the dispatcher's token balances) calls `CallDispatcher.dispatch(abi.encode([Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly — there is no access control preventing this. [2](#0-1) 
4. Token `X` is transferred out of the shared `CallDispatcher` to the attacker, even though it originated from funds intended for the order's beneficiary/protocol dust accounting.

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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-328)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L427-436)
```text
    /**
     * @dev Executes arbitrary calldata attached to an order's output via the CallDispatcher.
     * After dispatching the calls, any residual token balances left on the dispatcher
     * are swept back to this contract and accounted for as protocol dust.
     *
     * This enables composable order fulfillment — solvers can route through DEXes,
     * lending protocols, or other DeFi primitives as part of filling an order.
     *
     * @param order The order containing the output calldata to execute.
     * @param outputsLen The number of output assets to sweep after execution.
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-484)
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
```
