### Title
Unrestricted `dispatch()` on the shared `CallDispatcher` lets anyone steal tokens/ETH left in transit during cross-chain calldata execution - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` executes arbitrary attacker-supplied `Call[]` with **no access control at all** — no `onlyOwner`, no caller allowlist, nothing. This mirrors the root cause of the referenced report (a privileged actor with unrestricted arbitrary-execution rights over custodied user funds), except here the privilege is granted to *anyone*, not just an admin. Multiple protocol apps (`HyperFungibleToken`, `WrappedHyperFungibleToken`, `IntentGatewayV2`/`IntentsBase`) route bridged funds *through* this same contract as an intermediate custodian before the calldata-execution step, and the documentation itself acknowledges the dispatcher "holds tokens temporarily during execution."

### Finding Description
`CallDispatcher.dispatch(bytes memory encoded)` decodes a `Call[]` and executes each call from the dispatcher's own context: [1](#0-0) 

There is no modifier restricting `msg.sender` — any EOA or contract can call this function directly on the deployed `CallDispatcher` instance.

This contract is used as a shared, deterministic (CREATE2, same salt) custody point across independent apps:
- `HyperFungibleToken`/`WrappedHyperFungibleToken` unlock/mint bridged tokens directly *to the dispatcher address* so that attached calldata can spend them, per the documented flow ("Setting `to` to the `CallDispatcher` address ensures the dispatcher holds the tokens... so subsequent calls can spend them"): [2](#0-1) 
- `IntentsBase._execute` sends order-output assets to the dispatcher, invokes `dispatch(order.output.call)`, and only sweeps back the specific tokens listed in `order.output.assets` — any other token/ETH balance left behind (dust from slippage, unexpected swap outputs, partial spends) is not swept: [3](#0-2) 

Because the same `CallDispatcher` deployment is shared across these apps (see `DeployHFT.s.sol` / `DeployWrappedHFT.s.sol` / `DeployIsmp.s.sol`, all using the same CREATE2 `salt`), and its `dispatch()` entrypoint is public, any balance transiently or accidentally sitting on the dispatcher — including native ETH sent via `receive()` — can be swept out by an unrelated, unprivileged attacker simply by calling `dispatch()` themselves with a `Call` that transfers the balance to their own address, before the legitimate app's sweep/governance logic runs.

The documentation itself flags the danger of unlimited approvals precisely because of this shared, unauthenticated custody window: [4](#0-3) 

### Impact Explanation
This is a direct "stealing or loss of funds" / "unauthorized transaction or execution" bug matching the bounty scope: any token or native balance that ends up on the `CallDispatcher` — whether from slippage/dust in an `IntentsBase`/`IntentGatewayV2` fill, an under-consumed HFT/WrappedHFT calldata-execution transfer, or a plain mistaken direct transfer to the (publicly known, deterministic) dispatcher address — is not protected by any access control and can be drained by an arbitrary unprivileged caller before the legitimate sweep or governance `SweepDust`/withdrawal path executes. Since the dispatcher is shared across multiple deployed apps, funds belonging to unrelated users/protocols passing through the same instance are all exposed to the same open door.

### Likelihood Explanation
High for the "dust after imperfect calldata execution" path: any composable order fill or bridge-and-swap operation whose attached `Call[]` doesn't consume 100% of the transferred/unlocked amount (common with slippage-bounded swaps, partial approvals, or multi-step compositions) leaves a residual balance on the dispatcher with zero protection window — an attacker only needs to front-run/observe the dispatcher's balance and call `dispatch()` themselves. No privileged role, relayer collusion, or malicious peer is required — `dispatch()` is a fully public function callable by any EOA.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by the specific app(s)/owner that are authorized to route funds through it (e.g., an `onlyAuthorizedCaller` allowlist set at construction/config time, mirroring the `onlyHost`/`restrict` patterns already used elsewhere in the codebase such as `HostManager.restrict`). Alternatively, deploy per-call ephemeral dispatcher instances (e.g., CREATE2 with a one-time salt tied to the specific execution) so no shared, globally-addressable custody contract exists, and ensure any residual balance is force-swept back to the invoking app atomically within the same call rather than relying on best-effort sweeps of only the known asset list.

### Proof of Concept
1. An `IntentsBase`/`IntentGatewayV2` order fill executes `order.output.call` via `dispatcher.dispatch(order.output.call)`, where the attached call is a UniswapV2 swap with slippage protection that leaves `X` amount of an intermediate token unspent on the dispatcher (`IntentsBase.sol:441-442`).
2. `_execute` only sweeps tokens present in `order.output.assets` (the declared output list); the leftover intermediate token `X` is not in that list and remains on the dispatcher (`IntentsBase.sol:447-473`).
3. An attacker, observing `IERC20(X).balanceOf(dispatcher)` > 0, calls `CallDispatcher.dispatch()` directly (no permission required) with `Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})`.
4. `CallDispatcher.dispatch` executes the call from its own context and transfers the leftover token balance to the attacker, since `dispatch()` performs no `msg.sender` check: [1](#0-0)

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

**File:** docs/content/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token.mdx (L160-203)
```text
### Calldata Execution

Pass a non-empty `data` field to execute arbitrary calls on the destination chain after tokens are unlocked. The `data` must be an ABI-encoded `Call[]` array (see [overview](/developers/evm/hyper-fungible-token/overview#calldata-execution) for the full `Call` struct and security details).

When `isWeth = true`, the WrappedHFT unwraps WETH to native ETH on receive. This example bridges WETH back to the home chain, where it's unwrapped to native ETH and swapped for an exact amount of USDC via UniswapV2. The `Call.value` field forwards the native ETH to the router — demonstrating that the `CallDispatcher` can hold and forward native tokens:

```solidity lineNumbers
import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";

address[] memory path = new address[](2);
path[0] = WETH;
path[1] = USDC;

Call[] memory calls = new Call[](1);

// Swap native ETH → exact USDC via UniswapV2
// The CallDispatcher holds the unwrapped ETH and forwards it via Call.value
calls[0] = Call({
    to: UNISWAP_V2_ROUTER,
    // forward the native ETH to the router
    value: amount,
    data: abi.encodeWithSelector(
        IUniswapV2Router02.swapETHForExactTokens.selector,
        usdcAmountOut,
        path,
        recipientAddress,
        block.timestamp
    )
});

IHyperFungibleToken(address(wrapper)).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(1),
        // unlock to the CallDispatcher so it receives the unwrapped ETH
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```

Tokens are unlocked (or unwrapped for WETH) to `to` first, then the `CallDispatcher` executes each call in sequence. Setting `to` to the `CallDispatcher` address ensures the dispatcher holds the tokens (or native ETH) so subsequent calls can spend them via `Call.value` or ERC20 transfers.
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

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```
