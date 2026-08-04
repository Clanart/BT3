Based on my investigation, I found a real, exact analog to the AssetManager approval-leftover pattern: the shared **`CallDispatcher`** contract used by both the Intent Gateway (`evm/src/apps/intentsv2/IntentsBase.sol`) and the HyperFungibleToken apps executes attacker/user-supplied `Call[]` arrays "as itself," and it is a **single shared, stateful contract reused across every order fill and every cross-chain transfer** on a chain. Any ERC20 `approve()` call embedded in one order's `postdispatch`/`predispatch`/HFT calldata permanently grants an allowance from the `CallDispatcher` to whatever spender the order author chose — and nothing ever revokes it. This is functionally identical to AssetManager leaving stale `moneyMarkets`/token approvals after removal: a shared custodial contract accumulates unlimited, un-cleared allowances to addresses that only had legitimate access to *one specific* transient balance.

### Title
Persistent unlimited ERC20 approvals left on the shared `CallDispatcher` allow future users' bridged/escrowed funds to be stolen - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` executes an arbitrary, caller-supplied `Call[]` array in its own storage/allowance context [1](#0-0) . It is a single, permanently-deployed contract shared by every Intent Gateway order (`predispatch`/`postdispatch`) and every `HyperFungibleToken`/`WrappedHyperFungibleToken` cross-chain transfer's calldata field [2](#0-1) [3](#0-2) . Any order/message author can include a `Call` that does `IERC20(token).approve(attackerSpender, type(uint256).max)`. Since the contract keeps no allowance-tracking or revocation logic, that approval persists indefinitely on the dispatcher's own address, exactly like the AssetManager's `removeAdapter`/`removeToken` leaving stale allowances in place.

### Finding Description
`CallDispatcher` is documented as executing calls "in its own context (not via delegatecall)... the dispatcher contract holds tokens temporarily during execution" [4](#0-3) , and the docs explicitly warn integrators to "use exact amounts rather than unlimited allowances" — but this is only advisory guidance to *order authors*, not an enforced contract invariant. Nothing in `CallDispatcher.sol` or `IntentsBase._execute` limits, tracks, or resets approvals made during a call batch [5](#0-4) .

The foundry test `testPostdispatchTokenSweep` demonstrates the exact pattern: a postdispatch call does `IERC20.approve(uniswapRouter, type(uint256).max)` on behalf of the dispatcher [6](#0-5) . After the swap, the sweep logic (`_execute`) only sweeps *token balances*, never touches or revokes approvals [7](#0-6) . The unlimited `approve(uniswapRouter, max)` set by this order remains active on `CallDispatcher` forever.

Because `CallDispatcher` is the same contract address for every subsequent order/fill/transfer that routes tokens through it (per `to: CALL_DISPATCHER` in HFT calldata, or `beneficiary: dispatcher` in intent orders), an unprivileged attacker can:
1. Place a trivial order (or trigger an HFT calldata-execution transfer) whose `Call[]` grants `approve(attackerAddress, type(uint256).max)` for some commonly-bridged token (e.g., USDC) from the `CallDispatcher`.
2. Wait for any *future, unrelated* user's order/transfer to route the same token through `CallDispatcher` (e.g., `beneficiary: dispatcher` for a Uniswap-based postdispatch swap, or an HFT mint-to-dispatcher-then-swap flow) — a normal, legitimate usage pattern shown in the tests.
3. As soon as that token balance briefly lands on `CallDispatcher` (before the sweep/transfer executes), call `transferFrom(dispatcher, attacker, amount)` using the leftover unlimited approval, front-running or racing the dispatcher's own sweep calls within the same or an adjacent transaction window (since the approval is already granted, this doesn't need any privileged relayer/prover role — the attacker uses their own approval directly against a shared custody contract).

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. `CallDispatcher` transiently custodies real user funds (solver outputs, bridged HFT tokens) for every order/transfer on the chain. A stale unlimited approval set by one (potentially malicious) order author lets that same attacker drain funds belonging to *any other, unrelated user's order* that happens to route the same ERC20 through the shared dispatcher afterward — a direct fund-theft / wrong-beneficiary path, matching the bounty's "stealing or loss of funds" and "transaction manipulation" categories.

### Likelihood Explanation
Likelihood is meaningful because:
- Placing an order or triggering an HFT calldata-execution send is a fully permissionless, unprivileged action — no relayer, prover, or admin involvement is required to set up the malicious approval.
- The dispatcher is explicitly a shared, long-lived, singleton contract (per the docs: "Existing `CallDispatcher` deployments are listed on the contract addresses page") reused across all orders and HFT transfers on a chain, so the attack surface accumulates over the contract's lifetime.
- The only mitigation currently offered is a documentation *recommendation* to use exact approvals, not an enforced contract-level guard — any order author can ignore it.

### Recommendation
Have `CallDispatcher.dispatch()` (or the calling contracts `IntentsBase._execute` / HFT `onAccept`) explicitly revoke any ERC20 approvals granted during the batch before returning — e.g., by tracking which `(token, spender)` pairs were approved during the call sequence and resetting them to zero after execution and after the sweep, mirroring the audit's recommended `removeApprovals`/`removeTokenApprovals` pattern. Alternatively, restrict `CallDispatcher` to non-persistent, single-use ephemeral instances (e.g., deploy a minimal proxy per dispatch and self-destruct/discard after use) so no approval state can carry over between unrelated orders.

### Proof of Concept
1. Attacker places an Intent Gateway order (or triggers an HFT `send` with calldata) whose output/message `data` is a `Call[]` containing: `Call({to: USDC, value: 0, data: abi.encodeWithSelector(IERC20.approve.selector, attackerEOA, type(uint256).max)})`, with `beneficiary`/`to` set to the shared `CallDispatcher` address, following exactly the pattern in `testPostdispatchTokenSweep` [6](#0-5) .
2. This order fills/settles normally; `CallDispatcher` now holds `allowance(CallDispatcher, attackerEOA) = type(uint256).max` for USDC, and no code path clears it [8](#0-7) .
3. Later, any other user places an order using USDC as an output with `beneficiary: dispatcher` (a legitimate, documented usage pattern for postdispatch swaps), or an HFT transfer mints/unlocks USDC to `CALL_DISPATCHER` for calldata execution.
4. When the solver/relayer delivers that fill, USDC briefly lands on `CallDispatcher` before its own postdispatch calls/sweep run.
5. The attacker calls `USDC.transferFrom(dispatcher, attackerEOA, balance)` directly (independent transaction, using the pre-existing approval from step 1) to seize the victim's funds before or interleaved with the dispatcher's own sweep transfer, since `CallDispatcher` itself never checks or clears outstanding allowances.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L41-62)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-474)
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

```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L330-333)
```text

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L1219-1224)
```text
        // Call 1: Approve Uniswap router
        postdispatchCalls[0] = Call({
            to: address(usdc),
            value: 0,
            data: abi.encodeWithSelector(IERC20.approve.selector, uniswapRouter, type(uint256).max)
        });
```
