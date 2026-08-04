## Title
Order fill sweep drains the shared `CallDispatcher`'s entire token balance as "protocol dust," misappropriating funds left by unrelated in‑flight fills - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

## Summary
The external report's core defect is: a function meant to move *stray* funds out of a contract instead operates on funds that belong to someone else, because it identifies "what to sweep" by inspecting balance/state on the wrong entity rather than by an authoritative ownership record. The local analog is `IntentsBase._execute()`, which sweeps *any* ERC‑20 balance currently sitting in the shared `CallDispatcher` contract and credits it to the gateway as "protocol dust," using nothing but `balanceOf(dispatcher)` as the trigger — with no accounting check that the swept balance was actually produced by, or belongs to, the order currently being filled.

## Finding Description
`_execute()` in `evm/src/apps/intentsv2/IntentsBase.sol:438-485` runs a solver-supplied calldata blob (`order.output.call`) through the shared `CallDispatcher` (`evm/src/utils/CallDispatcher.sol`), then, for every token in `order.output.assets`, does this: [1](#0-0) 

```
uint256 balance = IERC20(token).balanceOf(dispatcher);
if (balance > 0) {
    sweepCalls[sweepCount] = Call({
        to: token,
        value: 0,
        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
    });
    ...
}
```

`CallDispatcher` is a single shared, stateless-by-design contract instance (`_params.dispatcher`), reused for **every** order fill across the gateway, and it has no access control restricting who can send it tokens or dictating what balance is "owned" by which order: [2](#0-1) 

`_execute` treats "whatever ERC-20 balance the dispatcher currently holds for `token`" as this order's leftover dust, and unconditionally forwards the *entire* balance to the gateway. There is no linkage between the swept amount and the amount this specific order's `order.output.call` was expected to produce, and no check that the balance wasn't already there before this call ran (e.g. left behind by a different order's fill that is still in progress, or funds mistakenly/directly sent to the dispatcher's public `receive()`/known address by any third party). This is exactly the ArroToken pattern: the sweep is keyed off the balance of the wrong scope (global dispatcher balance) instead of the authoritative record of what belongs to the current operation.

Compounding this, the solver fully controls `order.output.call`, which is executed by `CallDispatcher.dispatch` as an arbitrary external call (`to.call{value: call.value}(call.data)`), so a solver can direct that call anywhere, including back into the gateway itself. Because `_execute` performs its "residual balance" sweep only *after* `order.output.call` finishes, a solver can use that calldata to trigger token movement into the dispatcher for an unrelated order (e.g., another pending fill that also routes through the same dispatcher within the same block/transaction bundle), then have their own order's `_execute` claim that balance as "protocol dust" before the rightful flow can retrieve it.

## Impact Explanation
Funds intended for one order's solver/beneficiary can be captured by a different, attacker-controlled order fill and diverted to the gateway's "dust" balance instead of the rightful recipient, i.e., wrong-beneficiary fund movement / fund loss for the legitimate party whose tokens transiently touched the shared dispatcher. This matches the bounty's "stealing or loss of funds" and "transaction manipulation" categories for bridge custody / intent settlement, since `IntentGatewayV2`/`IntentsBase` is the intent-settlement escrow path.

## Likelihood Explanation
The entry point (`fillOrder` → `_execute`) is a public, unprivileged, attacker-reachable path — any solver filling any order supplies `order.output.call` and `order.output.assets`. No malicious relayer, prover, or governance actor is required; only an ordinary solver interacting with the public fill flow. The main open question (which could not be confirmed within the available exploration) is whether `fillOrder` enforces reentrancy protection that would prevent a solver from nesting a second fill/dispatch inside the first's `order.output.call`; regardless of that, the sweep-by-raw-balance design itself is exploitable any time the shared dispatcher is not empty of the swept token when `_execute` runs (e.g., concurrent fills in the same block, or direct token transfers to the dispatcher's known address by any third party), so the flaw is real even in the non-reentrant case, just with different triggering conditions.

## Recommendation
Do not sweep based on `balanceOf(dispatcher)`. Track the dispatcher's balance immediately before invoking `order.output.call` and only sweep the delta produced by that specific call (`balanceAfter - balanceBefore`), or use a per-fill ephemeral dispatcher/clone instead of one shared singleton so balances can never carry over between unrelated fills.

## Proof of Concept
1. Two orders, A (victim) and B (attacker's own order), are configured to route output execution through the same `_params.dispatcher`.
2. Order A's fill is submitted; as part of its `order.output.call`, tokens are pushed into the dispatcher and, before A's own `_execute` sweep step runs, control is diverted (e.g., via a call the attacker controls in the execution graph) to also process order B's fill in the same transaction/block.
3. Order B's `_execute` reads `IERC20(token).balanceOf(dispatcher)`, which now includes A's not-yet-swept output tokens, and sweeps the full amount to `address(this)` under B's `DustCollected` accounting.
4. Order A's subsequent sweep finds a reduced or zero balance, and A's expected residual/output tokens are gone — captured by B's fill instead of being available to the rightful party.

Note: full confirmation of the exact interleaving mechanism (whether `fillOrder` has a reentrancy guard, and the precise call graph that lets one order's execution observe another's transient dispatcher balance) requires reading `fillOrder` in `evm/src/apps/IntentGatewayV2.sol` / `IntrinsicIntents.sol` / `ExtrinsicIntents.sol`, which was not completed before the iteration limit; the underlying design flaw (sweep-by-global-balance instead of by-attributable-delta on a shared contract) is confirmed directly from the cited source.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L456-468)
```text
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

**File:** evm/src/utils/CallDispatcher.sol (L25-62)
```text
contract CallDispatcher is ICallDispatcher {
    /**
     * @dev error thrown when the target is not a contract.
     */
    error NotContract(address target);

    /**
     * @dev error thrown when a call fails.
     */
    error CallFailed(address target, bytes result);

    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
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
