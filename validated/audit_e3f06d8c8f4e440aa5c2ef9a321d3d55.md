## Analysis

The 0x report's core broken invariant: **code that only tracks a specific declared token set, while the actual execution path (arbitrary calldata / swaps) can leave *other* tokens in an intermediate holding contract that the accounting logic never looks at — and that intermediate contract lets anyone extract what it's holding.**

The direct Hyperbridge analog is the `CallDispatcher` used by `IntentGatewayV2` (and `HyperFungibleToken`) for pre/postdispatch calldata execution.

### Title
Unrestricted `CallDispatcher.dispatch()` combined with narrow output-only dust sweep allows theft of stranded tokens - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` has no access control — any external account can call it and force the dispatcher to execute arbitrary `Call[]` against any contract, including ERC20 tokens the dispatcher happens to be holding. Meanwhile, `IntentsBase._execute()` only sweeps back the tokens explicitly declared in `order.output.assets` after postdispatch calldata runs, exactly mirroring the 0x bug pattern of trusting a narrow, declared output set while the actual execution (arbitrary swap calldata) can leave *other* tokens behind.

### Finding Description
`CallDispatcher.dispatch()` is a public entry point with no `onlyOwner`/`onlyGateway` restriction: [1](#0-0) . It executes any `Call{to, value, data}` supplied by the caller, forwarding arbitrary calldata to arbitrary contracts on the dispatcher's behalf.

The dispatcher is used as a shared temporary holding contract for both predispatch (swap-then-escrow) and postdispatch (fill-then-act) calldata: [2](#0-1) . After postdispatch execution, `IntentsBase._execute()` sweeps residual balances back to the gateway — but only for the tokens explicitly listed in `order.output.assets`, iterating `i < outputsLen`: [3](#0-2) .

This mirrors the 0x flaw precisely: if the postdispatch `Call[]` routes through a DEX (as the documented example patterns and `testPostdispatchTokenSweep`/Uniswap examples show — e.g. `evm/tests/foundry/IntentGatewayV2Test.sol:1193-1260`) and the swap/refund logic leaves behind a token that is *not* one of `order.output.assets` (e.g., an intermediate swap-path token, leftover input token from an approve-then-swap sequence, or any token accidentally sent to the dispatcher by a misconfigured call), that balance is invisible to `_execute()`'s sweep — it is never accounted for and never returned to the gateway.

Because `CallDispatcher.dispatch()` is callable by anyone with no restriction, any attacker can then directly call `dispatch()` with a `Call{to: strandedToken, data: transfer(attacker, balance)}` and drain that stranded balance. Worse, since the same `CallDispatcher` instance is shared across all orders/fillers/predispatch and postdispatch calls system-wide, an attacker does not need to wait for a stray leftover — they can race any in-flight predispatch/postdispatch execution: between the moment predispatch assets are transferred into the dispatcher and the moment the swap-calls complete, or between postdispatch execution and the sweep, an attacker's own `dispatch()` call can be interleaved (same-block, different transaction ordering) to redirect tokens mid-flow, since nothing gates `dispatch()` to only the `IntentGatewayV2`/`HyperFungibleToken` contract.

### Impact Explanation
Any token balance that transiently sits in the `CallDispatcher` — whether from a documented swap-then-escrow/fill-then-act flow leaving an unaccounted residual, or from an attacker exploiting transaction ordering — can be stolen outright by an unprivileged caller, since `dispatch()` performs no caller check and no token-destination check. This is direct, unauthorized theft of user/solver/protocol funds routed through a core, documented composability primitive of the Intent Gateway and Hyper Fungible Token systems.

### Likelihood Explanation
`dispatch()` having zero access control is a straightforward, always-reachable public-entrypoint bug — no privileged actor, prover, or relayer collusion needed. The precondition (a non-zero token balance sitting in `CallDispatcher`) is a normal and encouraged usage pattern (predispatch swap-then-escrow, postdispatch fill-then-act, transfer-and-swap HFT flows) documented across multiple integration guides, and the sweep logic is provably narrow (only iterates declared `order.output.assets`), so the "unaccounted token left behind" precondition is realistic, not a required attacker-controlled edge case.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by registered/authorized caller contracts (e.g., `IntentGatewayV2`, `HyperFungibleToken`/`WrappedHyperFungibleToken` instances) via an allowlist or `onlyRole` modifier, rather than leaving it a bare public function. Additionally, broaden `_execute()`'s post-call sweep to enumerate and reclaim *any* residual ERC20/native balance left in the dispatcher — not just tokens in `order.output.assets` — so unexpected tokens from swap calldata cannot silently strand there.

### Proof of Concept
1. Deploy/observe the shared `CallDispatcher` instance used by `IntentGatewayV2` per `evm/src/apps/intentsv2/IntentsBase.sol:438-468`.
2. A solver fills an order whose `order.output.call` swaps USDC→DAI via Uniswap but the swap path or router behavior leaves a small residual of an intermediate/path token (or excess input token) in the `CallDispatcher` that is not part of `order.output.assets` — `_execute()`'s loop at lines 447-473 never inspects or sweeps it.
3. An attacker (any address, no special role) directly calls:
```solidity
Call ;
calls[0] = Call({
    to: strandedToken,
    value: 0,
    data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, IERC20(strandedToken).balanceOf(address(callDispatcher)))
});
CallDispatcher(callDispatcher).dispatch(abi.encode(calls));
```
Because `CallDispatcher.dispatch()` performs no `msg.sender` check (`evm/src/utils/CallDispatcher.sol:44-62`), this call succeeds and transfers the stranded balance to the attacker. [1](#0-0) [3](#0-2)

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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L97-111)
```text
### Predispatch

The `predispatch` field in `Order` contains calldata to execute *before* escrowing inputs. The predispatch assets specified in `DispatchInfo.assets` are transferred to the `CallDispatcher`, the encoded calls are executed, and the resulting tokens are transferred back to the gateway for escrow. This enables swap-then-escrow patterns — for example, a user sends ETH which the `CallDispatcher` swaps to DAI on Uniswap, and the resulting DAI is escrowed as the order input.

### Postdispatch

The `call` field in `PaymentInfo` contains calldata to execute *after* the order is filled. This enables fill-then-act patterns — for example, output tokens received from the solver are routed through a DeFi protocol before reaching the beneficiary.

Execution timing differs by mode:

- **Same-chain**: Calldata executes only after the order is **fully filled**. Partial fills do not trigger calldata — only the final fill that completes the order executes it. This ensures all output tokens are available when the calls run.
- **Cross-chain**: Calldata executes **immediately** after the solver delivers output tokens to the beneficiary, before the settlement message is dispatched back to the source chain.

After execution, any tokens remaining in the `CallDispatcher` are swept back to the gateway and collected as dust (emitting `DustCollected` for each token). When postdispatch calldata is present, 100% of any surplus (solver overpayment) goes to the protocol rather than being split with the beneficiary — this prevents manipulation of surplus distribution through calldata side effects.

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-468)
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
```
