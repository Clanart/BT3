### Title
`CallDispatcher.dispatch()` is a fully public, unauthenticated arbitrary-call executor that can be invoked directly by any caller - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
The reported OptimismPortal bug is fundamentally about an unprivileged message-passing entrypoint allowing an attacker to choose an arbitrary `target`/call context for a privileged, shared executor, letting funds or privileged calls be misdirected. Hyperbridge's local analog is `CallDispatcher`, the shared cross-chain "execute arbitrary calldata on the destination chain" primitive used by `HyperFungibleToken`, `WrappedHyperFungibleToken`, and `IntentGatewayV2`. Its `dispatch()` function has **no caller restriction at all** — unlike `HostManager.onAccept` and `BandwidthManager.onAccept`, which both gate execution with `restrict(_params.host)` / `onlyHost` plus a `request.source == hyperbridge` check, `CallDispatcher.dispatch(bytes memory encoded)` is `external` with zero access control and can be called by anyone, not just by the bridge apps that are supposed to forward already-authenticated cross-chain calldata to it. [1](#0-0) 

### Finding Description
`CallDispatcher` is meant to be invoked only from within `onAccept` of a bridging app after that app has verified the cross-chain message's source (e.g. `WrappedHyperFungibleToken.onAccept` checks `expectedSource`/`request.from` before calling `ICallDispatcher(_dispatcher).dispatch(message.data)`): [2](#0-1) 

However, the authentication happens in the *caller* (the token contract), not in `CallDispatcher` itself. `CallDispatcher.dispatch()` decodes an attacker-supplied `Call[]` and executes `to.call{value: call.value}(call.data)` for each entry, using `CallDispatcher`'s own identity (`msg.sender == CallDispatcher`) and its own ETH balance (accepted unconditionally via the payable `receive()`), with no check on who is calling `dispatch()` and no binding to any specific bridged message, source chain, or module. This mirrors the `OptimismPortal` flaw precisely: a shared, privileged execution primitive that should only ever be reached through an authenticated path is instead a bare public entrypoint, so anyone can supply their own `target`/calldata and drive execution "as" the trusted dispatcher.

This differs materially from the two governance-style modules in the same codebase that face the identical threat model (arbitrary incoming action payload) and correctly lock it down: [3](#0-2) [4](#0-3) 

`CallDispatcher` has no equivalent of these checks.

### Impact Explanation
Because `CallDispatcher` is a shared singleton wired into multiple production apps (`HyperFungibleToken`, `WrappedHyperFungibleToken`, `IntentGatewayV2`), any native ETH balance it accumulates (dust from failed native pushes, refunded ETH from `WrappedHyperFungibleToken`'s WETH-unwrap-then-fallback path, or ETH intentionally routed through it as part of a cross-chain call's `value`) can be drained or redirected by an unrelated, unauthenticated caller who invokes `dispatch()` directly with their own `Call[]` — not the payload that was actually bridged. Because the downstream call is made with `msg.sender == CallDispatcher`, this also lets an attacker impersonate the dispatcher's trusted identity against any third-party contract that grants `CallDispatcher` special treatment (e.g. an approval), producing unauthorized execution/fund movement that the destination contract cannot distinguish from a legitimately bridged instruction.

### Likelihood Explanation
`dispatch()` requires no privilege, no proof, and no relayer — it is directly callable by any EOA against the deployed `CallDispatcher` address, which is a fixed, publicly known contract address referenced by multiple deployed apps. The only preconditions are (1) the target of the attacker's fabricated `Call` has code (`extcodesize != 0`), and (2) sufficient ETH balance sits in `CallDispatcher` for a value-bearing attack, or a downstream contract trusts `msg.sender == CallDispatcher` for a non-value attack. Given the contract explicitly accepts ETH via `receive()` with no accounting of "whose" ETH it is, this is straightforward to trigger.

### Recommendation
Restrict `CallDispatcher.dispatch()` so it can only be invoked by the bridging app(s) that are supposed to forward already-authenticated calldata (e.g., an allow-listed set of caller apps, or make each app deploy/own its own `CallDispatcher` instance instead of sharing one), and remove or account for the unconditional `receive()` so the contract never holds unattributed ETH. At minimum, mirror the pattern used by `HostManager`/`BandwidthManager`: bind execution to a specific authenticated caller and never let it be invoked as a bare public function.

### Proof of Concept
1. Locate the deployed `CallDispatcher` address used by any live `HyperFungibleToken`/`WrappedHyperFungibleToken`/`IntentGatewayV2` deployment (it's referenced publicly by these contracts' configuration).
2. Observe/wait for the dispatcher to hold a non-zero ETH balance (e.g., from a `WrappedHyperFungibleToken.onAccept` native-push fallback path where `sent` briefly fails before re-wrapping, or from any other flow that sends value into it).
3. Call `CallDispatcher.dispatch(encoded)` directly (bypassing any bridging app and any ISMP proof) with `encoded = abi.encode([Call({to: attackerContract, value: currentBalance, data: ""})])`.
4. `dispatch()` executes `attackerContract.call{value: currentBalance}("")` immediately since there is no caller check — the attacker extracts the dispatcher's ETH balance with a normal transaction, no relayer, prover, or admin involved.

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

**File:** evm/src/core/HostManager.sol (L95-98)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```

**File:** evm/src/apps/BandwidthManager.sol (L201-204)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        PostRequest calldata request = incoming.request;

        if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();
```
