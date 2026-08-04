### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain non-declared token dust left behind by order calldata execution - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`IntentGatewayV2`/`IntentsBase` route all order-level calldata (predispatch and postdispatch) through a single, gateway-wide `CallDispatcher` instance (`_params.dispatcher`). After executing an order's `output.call`, the gateway sweeps back only the balances of the tokens explicitly declared in that order's `output.assets` list [1](#0-0) . But `CallDispatcher.dispatch()` itself has no caller restriction whatsoever [2](#0-1) , so any balance of a token that isn't in that fixed sweep list — left behind by attacker-crafted or third-party calldata that swaps into an undeclared token — sits in the shared dispatcher and can be pulled out by literally anyone with a direct, unprivileged call. This is structurally the same primitive as the Malt finding: a contract that the protocol treats as a trusted/whitelisted custody hop exposes a public entrypoint that lets outsiders extract value the intended flow never authorized them to touch.

### Finding Description
`IntentsBase._execute()` is the only code path that returns funds out of the `CallDispatcher` on behalf of the protocol. It:
1. Dispatches the order's attacker/order-creator-supplied `order.output.call` through the shared `CallDispatcher` [3](#0-2) .
2. Sweeps residual balances back to the gateway, but only for the tokens listed in `order.output.assets[0..outputsLen)` — the declared output token set of that specific order [4](#0-3) .

`CallDispatcher` is a single, gateway-wide singleton (`_params.dispatcher`, set once via governance `_updateParams` and shared by every order that carries calldata) [5](#0-4) . Its `dispatch(bytes)` function accepts an arbitrary `Call[]` and executes each call unconditionally against any contract with code — there is no `onlyGateway`/`msg.sender` check, and no reentrancy lock:
```solidity
function dispatch(bytes memory encoded) external {
    Call[] memory calls = abi.decode(encoded, (Call[]));
    ...
    (bool success, bytes memory result) = to.call{value: call.value}(call.data);
    if (!success) revert CallFailed(to, result);
}
``` [6](#0-5) 

The gateway's design implicitly assumes only *it* ever needs to move funds through the dispatcher and that whatever lands there in the declared output/predispatch tokens gets swept back atomically. That assumption breaks the moment an order's `output.call` produces (via an on-chain swap, claim, or any external call) a balance in a token that is **not** in `order.output.assets` — that balance is now permanently outside the gateway's sweep loop, sitting in a contract with an unauthenticated withdrawal entrypoint. Any address — not the order's solver, not the gateway, not governance — can call `CallDispatcher.dispatch()` directly with a `Call[]` such as `{to: token, data: transfer(attacker, balance)}` and take it.

This mirrors the Malt pattern precisely: the "whitelisted"/trusted intermediary (`UniswapHandler` there, `CallDispatcher` here) exposes public functions with no caller gating, so a control the system relies on (here: "only declared output tokens ever move, and only through the gateway's sweep") is trivially bypassed by calling the intermediary's public function directly.

### Impact Explanation
Any token balance stranded in the shared `CallDispatcher` — whether from a legitimate order's calldata producing an unexpected intermediate token, a swap leaving slippage residue in a token outside the declared output set, or an adversarial order deliberately engineered to leave dust in an undeclared token — is permanently and unauthorizedly extractable by any address. This is direct, unauthorized loss of funds from a bridge-adjacent custody contract that the protocol treats as trusted infrastructure, matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories. Because `CallDispatcher` is shared across *all* orders on the gateway, the blast radius is not limited to a single order's creator — any user whose calldata inadvertently (or an attacker's calldata deliberately) leaves non-declared-token balance on the dispatcher loses that value to the first unprivileged caller of `dispatch()`.

### Likelihood Explanation
No privileged role, relayer, prover, or governance actor is needed — the attacker only needs to call a public, unauthenticated function (`CallDispatcher.dispatch`) directly. The precondition (a non-zero balance of a token outside `order.output.assets` sitting on the shared dispatcher) is realistic any time order calldata performs a swap/route whose output token differs from the order's declared output token, or leaves slippage/rounding residue in an intermediate token — a common occurrence in the "swap-then-escrow" / "fill-then-act" composable patterns this feature is explicitly designed to support (per the Intent Gateway docs' predispatch/postdispatch description).

### Recommendation
- Restrict `CallDispatcher.dispatch()` to be callable only by an authorized gateway address (e.g., an `onlyOwner`/`onlyGateway` modifier set at deployment), removing the public unauthenticated entrypoint.
- Alternatively, deploy an ephemeral/per-order dispatcher (e.g., via `CREATE2` per commitment) instead of one shared singleton, so no cross-order or externally-drainable balance can ever accumulate.
- Add a generic sweep that clears *any* residual ERC20/native balance on the dispatcher after executing `order.output.call`, not just the tokens listed in `order.output.assets`, and route it back to the gateway (to be collected as protocol dust) rather than leaving it discoverable and extractable by outsiders.

### Proof of Concept
1. Governance deploys `IntentGatewayV2` with a single shared `CallDispatcher` at address `D`.
2. Order `O1` (any user) is filled with `output.assets = [TokenA]` and `output.call` that swaps solver-provided `TokenA` into `TokenB` via a DEX and forwards most of `TokenB` to the beneficiary, but leaves rounding/slippage residue of `TokenB` on `D` (a token not in `order.output.assets`).
3. `_execute()` runs `ICallDispatcher(D).dispatch(order.output.call)`, then sweeps only `TokenA` balances on `D` — the `TokenB` residue is left untouched because the sweep loop only iterates `order.output.assets` [7](#0-6) .
4. Attacker (unrelated to `O1`) calls `D.dispatch(abi.encode([Call({to: TokenB, value: 0, data: transfer(attacker, D.balanceOf(TokenB))})]))` directly — this succeeds because `dispatch()` performs no caller check [6](#0-5) .
5. Attacker now holds `TokenB` that was never owed to them, extracted from shared bridge infrastructure with zero privilege and zero interaction with the IntentGateway's own authorization logic.

### Citations

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
