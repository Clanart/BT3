## Finding [1](#0-0) 

`CallDispatcher.dispatch()` is a completely permissionless function — no `onlyHost`, `onlyApp`, or any caller check — that executes an arbitrary array of `Call{to, value, data}` **in the CallDispatcher's own context** (not `delegatecall`), meaning any balance the dispatcher happens to hold (ETH or ERC20) can be sent anywhere the caller wants:

```solidity
function dispatch(bytes memory encoded) external {
    Call[] memory calls = abi.decode(encoded, (Call[]));
    ...
    (bool success, bytes memory result) = to.call{value: call.value}(call.data);
    if (!success) revert CallFailed(to, result);
}
```

`CallDispatcher` is a shared singleton reused by `IntentGatewayV2` and `HyperFungibleToken`/`WrappedHyperFungibleToken` across chains [2](#0-1) .

`HyperFungibleTokenUpgradeable.onAccept` mints incoming tokens to `to` (which, per the documented composability pattern, is set to the `CallDispatcher` address) and then invokes `dispatch(message.data)` on user-authored calldata — **with no sweep-back of unspent balance afterward**, unlike `IntentGatewayV2`, which explicitly sweeps dust back after `_execute`: [3](#0-2) 

The documented pattern confirms tokens are minted directly to the `CallDispatcher` so subsequent calls can spend them: [4](#0-3) 

If the attached calldata does not fully consume the minted amount (slippage, a partial swap, a call that transfers less than the full minted balance, or any calldata bug from the sender who composed the message off-chain), the residual tokens/ETH are stranded on `CallDispatcher`. Because `dispatch()` has **zero access control**, any unprivileged address — not the original sender, not a relayer, not the app that funded it — can call `CallDispatcher.dispatch()` directly with a `Call[]` such as `{to: token, data: transfer(attacker, strandedBalance)}` and sweep those funds to themselves.

### Why existing guards don't stop this
- `onlyHost` on `onAccept` only gates who can *trigger* the mint-then-dispatch flow; it does nothing to restrict who can call `dispatch()` afterward.
- `CallDispatcher` itself has no owner, no allow-list of callers, and no restriction tying a `dispatch()` invocation to a specific pending operation or app.
- `IntentGatewayV2`'s explicit dust-sweep (`_execute` in `IntentsBase.sol`) mitigates this for its own flow, but `HyperFungibleTokenUpgradeable.onAccept` has no equivalent sweep, so any leftover balance from the calldata-execution path is a standing, permanently-exploitable target — no relayer/peer compromise, no front-running window needed; it's simply an open door on shared custody.

### Impact
Direct theft of any token/ETH balance left on the shared `CallDispatcher`, which is used by every deployed `HyperFungibleToken`/`WrappedHyperFungibleToken` and `IntentGatewayV2` instance on a chain. This is the exact "residual state on a shared, permissionless custody contract can be drained by anyone" pattern from the BaseVault bug — the broken invariant is identical: a public function that moves the contract's own held assets without verifying the caller is the party entitled to them.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a registered set of caller apps (e.g., an `onlyAuthorizedCaller` mapping set by governance), and/or have every app that funds the dispatcher (especially `HyperFungibleTokenUpgradeable.onAccept`) sweep 100% of the dispatcher's balance for the tokens involved back to itself or the beneficiary at the end of the same transaction, mirroring `IntentsBase._execute`'s dust-sweep pattern.

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

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-336)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```

**File:** docs/content/developers/evm/hyper-fungible-token/hyper-fungible-token.mdx (L149-162)
```text
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

Tokens are minted to `to` first, then the `CallDispatcher` executes each call in sequence. If the calls need to spend the minted tokens, set `to` to the `CallDispatcher` address so tokens are minted directly to the dispatcher.
```
