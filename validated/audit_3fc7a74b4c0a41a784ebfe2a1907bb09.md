### Title
Unrestricted, shared `CallDispatcher.dispatch()` lets anyone sweep residual tokens/ETH left over from other users' cross-chain calldata executions - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher` is the exact local analog of the "malicious adapter" pattern from the external report: it is a public-entrypoint contract that executes arbitrary `Call[]` to arbitrary targets, with no whitelist, no `nonReentrant` guard, and no restriction on who may invoke it. Unlike Sense's Periphery adapters (which are *chosen* by the caller and thus only affect the caller), `CallDispatcher` is a single, permanently shared piece of infrastructure used by every `HyperFungibleToken`/`WrappedHyperFungibleToken` deployment on a chain [1](#0-0) . Any native ETH or ERC20 dust that ends up sitting in this contract between cross-chain calldata executions — a foreseeable outcome of the documented transfer-and-swap pattern — can be drained by *any* unprivileged address, because `dispatch()` has no access control at all.

### Finding Description
`CallDispatcher.dispatch(bytes memory encoded)` is `external` with no modifier restricting the caller to the ISMP host, to a specific `HyperFungibleToken` instance, or to any allowlist: [2](#0-1) 

It also has an unrestricted `receive() external payable`, explicitly documented as needed so the dispatcher "can hold and forward native tokens" during a transfer-and-swap flow [3](#0-2) .

The intended flow is: `HyperFungibleToken.onAccept` mints/unlocks bridged tokens directly to the dispatcher's address, then calls `ICallDispatcher(_dispatcher).dispatch(message.data)` so the dispatcher can, e.g., approve and swap those tokens in the same transaction [4](#0-3) . The docs themselves flag the custody risk but only mitigate the *approval* amount, not residual balances: "Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution" [1](#0-0) .

This "temporary" custody assumption is not enforced by any code — there is no accounting of what belongs to which pending operation, and no restriction on who can call `dispatch()`. Any of the following, all realistic given the documented usage patterns, leave value sitting in the dispatcher:
- A swap's `amountOutMin`/slippage leaves output tokens or unspent input tokens in the dispatcher rather than forwarding 100% to the final recipient.
- The WETH-unwrap-then-swap pattern sends native ETH to the dispatcher via `Call.value`, and any rounding/leftover ETH (e.g., `swapETHForExactTokens` refunds unused ETH to `msg.sender`, which is the dispatcher itself, not the end user) stays there permanently [5](#0-4) .
- Someone directly sends ETH/tokens to the dispatcher address by mistake, since `receive()` accepts any ETH unconditionally [6](#0-5) .

Because `dispatch()` is world-callable, an attacker simply builds a `Call[]` such as `{ to: token, value: 0, data: transfer(attacker, balance) }` or `{ to: attacker, value: address(dispatcher).balance, data: "" }` and calls `CallDispatcher.dispatch(abi.encode(calls))` directly — no proof, no host authorization, no relationship to the original bridging operation is required, since `dispatch()` never checks `msg.sender` against `IHost`/`HyperApp`'s `onlyHost` modifier the way `onAccept` does.

### Impact Explanation
This is exact fund loss for legitimate bridge users, matching the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories. Because `CallDispatcher` is shared infrastructure across every `HyperFungibleToken`/`WrappedHyperFungibleToken` deployment on a chain [1](#0-0) , dust accumulates from many independent, unrelated cross-chain transfer-and-swap operations over time, and is fully sweepable by any unprivileged attacker at any moment — not just the original sender or recipient. Unlike the Periphery.sol report where the caller opts into interacting with a chosen (and potentially malicious) adapter, here the danger is inverted and worse: the trusted, canonical dispatcher itself is the permissionless sink, and third parties can steal value that never belonged to them.

### Likelihood Explanation
High. No privileged role, relayer, prover, or malicious peer is required — only a normal EOA calling a public Solidity function with attacker-chosen calldata, exactly the "unprivileged attacker causing fund loss" profile the bounty prioritizes. The trigger condition (residual ETH/token balance in the dispatcher) is a natural consequence of the officially documented transfer-and-swap usage pattern, not a contrived edge case.

### Recommendation
- Restrict `CallDispatcher.dispatch()` so it can only be invoked in the same transaction/context as an authorized bridging app (e.g., require `msg.sender` to be a registered `HyperApp`, or use a transient/ephemeral proxy contract created per-call instead of a long-lived shared contract).
- Sweep any leftover native/ERC20 balance back to the intended beneficiary at the end of every `dispatch()` call rather than leaving custody indefinite.
- Add a `nonReentrant` guard and treat any balance left after a batch as unexpected/refundable only to the original recipient, never freely takeable by arbitrary callers.

### Proof of Concept
1. A user bridges via `HyperFungibleToken.send()` with `to = CALL_DISPATCHER` and `data` encoding a `Call[]` that approves and swaps the minted tokens via UniswapV2, per the documented pattern [7](#0-6) .
2. On the destination chain, `onAccept` mints tokens to the dispatcher and calls `dispatch(message.data)` [4](#0-3) . Due to slippage/rounding, some ERC20 dust or unswept native ETH refund remains in the `CallDispatcher` contract after the swap completes.
3. An attacker, monitoring `CallDispatcher`'s balances (a single well-known, permanent address per chain), calls `CallDispatcher.dispatch(abi.encode([Call({to: leftoverToken, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, dustAmount)})]))` directly.
4. Since `dispatch()` has no access-control modifier [8](#0-7) , this succeeds and transfers the dust to the attacker, repeatable indefinitely as new bridging operations leave new residue.

### Citations

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
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

**File:** docs/content/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token.mdx (L164-188)
```text
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
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L301-304)
```text

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** docs/content/developers/evm/hyper-fungible-token/hyper-fungible-token.mdx (L117-159)
```text
Bridge tokens and swap to WETH via UniswapV2 on the destination chain:

```solidity lineNumbers
import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";

address[] memory path = new address[](2);
path[0] = DEST_TOKEN;
path[1] = WETH;

Call[] memory calls = new Call[](2);

// Approve UniswapV2 router
calls[0] = Call({
    to: DEST_TOKEN,
    value: 0,
    data: abi.encodeWithSelector(IERC20.approve.selector, UNISWAP_V2_ROUTER, amount)
});

// Swap via UniswapV2
calls[1] = Call({
    to: UNISWAP_V2_ROUTER,
    value: 0,
    data: abi.encodeWithSelector(
        IUniswapV2Router02.swapExactTokensForTokens.selector,
        amount,
        minAmountOut,
        path,
        recipientAddress,
        block.timestamp
    )
});

IHyperFungibleToken(tokenAddress).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(42161),
        // mint to the CallDispatcher so the swap can spend the tokens
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```
