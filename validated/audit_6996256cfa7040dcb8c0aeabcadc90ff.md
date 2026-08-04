### Title
Missing module-identity (`from`) binding in `SimplexPaymaster.onAccept` allows any Hyperbridge-side account to trigger privileged upgrade/withdraw actions - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster` documents itself as being "administered exclusively through Hyperbridge governance," with every privileged action (`UpgradeContract`, `WithdrawAssets`, `RegisterToken`, `DeactivateToken`, `UpdateParams`) gated behind `onAccept`. However, `onAccept` only checks that the incoming request's **source chain id** equals the Hyperbridge state machine — it never checks the request's **`from`** field (the actual dispatching module/account on that chain). This is the same class of defect as the external `FirewallProxyIntercept` report: a privileged/administrative surface that is supposed to be reachable only by one authorized actor, but the enforcement mechanism only checks a coarse-grained property (chain identity) rather than fine-grained module identity, leaving the real authorization gap for anyone who can dispatch a message from that chain.

### Finding Description
`onAccept` in `SimplexPaymaster.sol` is: [1](#0-0) 

The only authentication check performed is:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
    revert UnauthorizedCall();
}
```
This validates the **chain-level** origin (that the message came from the Hyperbridge relay/parachain), but never validates **which module or account on that chain** dispatched the request (`incoming.request.from`). Once this single chain-id check passes, the decoded `RequestKind` is executed unconditionally — including:
- `UpgradeContract` → `ERC1967Utils.upgradeToAndCall(newImpl, initData)` (arbitrary implementation swap + arbitrary init call)
- `WithdrawAssets` → sweeps ERC-20 balances or the EntryPoint deposit to `treasury`

By contrast, other apps in this codebase that receive cross-chain governance messages (e.g. `IntentsBase`/`ExtrinsicIntents`) explicitly bind incoming requests to a specific registered module address via `request.from` and an `_instances` registry, not merely the source chain id. `SimplexPaymaster` skips this binding entirely, relying solely on the chain-id check — exactly mirroring the `FirewallProxyIntercept` problem where the security boundary between "admin-authorized path" and "any actor's path" is blurred because the enforcement point checks the wrong granularity of identity.

`HostManager.onAccept` has the identical gap: [2](#0-1) 
It also only checks `request.source.equals(hyperbridge())`, with no verification of `request.from`, before allowing `Withdraw` (moves bridge revenue to an attacker-chosen beneficiary) or `SetHostParam` (rewires `admin`, `handler`, `hostManager`, `consensusClient` of the whole `EvmHost`).

### Impact Explanation
If any account/module on the Hyperbridge chain (not just the intended governance pallet) can cause an ISMP `PostRequest` to be dispatched with `source` correctly set to the Hyperbridge state machine id but with `to` pointed at `SimplexPaymaster` (or the `EvmHost`'s configured `hostManager`), that party can:
- Redirect the paymaster's ERC-1967 proxy to an attacker-controlled implementation with `initData` executed immediately, then drain all ERC-20 allowances/balances and the EntryPoint deposit held by the paymaster (direct fund loss).
- Force `WithdrawAssets`/`Withdraw` calls that sweep protocol funds.
- Rewrite `EvmHost`'s critical parameters (`admin`, `handler`, `hostManager`, `consensusClient`) via `HostManager`, which is a full host-management takeover reachable through wrong module binding rather than an actual governance-authorized path.

This directly matches the "Hyperbridge Pivots" concern: cross-chain admin/host-management effects reachable through wrong module bindings or unauthenticated message flow, and results in unauthorized execution and fund loss.

### Likelihood Explanation
The check that is missing (`request.from` binding to a specific known governance/admin module id) is exactly the kind of check present elsewhere in the codebase (`ExtrinsicIntents`/`IntentsBase` module-binding via `_instances`), indicating the intended design requires per-module authorization, not just per-chain authorization. Its absence here is a straightforward, code-visible gap rather than a speculative one. Exploitability depends on whether the Hyperbridge relay chain permits any account/pallet other than the intended governance origin to dispatch an ISMP request with `source = hyperbridge` and attacker-chosen `to`/`body` — this repository does not contain the Substrate-side dispatch-authorization logic to fully confirm or rule this out, so the finding should be validated against the pallet-ismp dispatch authorization on the Hyperbridge chain side before treating it as fully proven end-to-end.

### Recommendation
In `SimplexPaymaster.onAccept` (and `HostManager.onAccept`), in addition to checking `incoming.request.source`, verify `incoming.request.from` against an explicitly configured/immutable governance module address (analogous to the `_instances` binding pattern used in `IntentsBase`), so that only the specific authorized Hyperbridge governance module — not merely "any message originating from the Hyperbridge chain" — can trigger `UpgradeContract`, `WithdrawAssets`, `SetHostParam`, or `Withdraw`.

### Proof of Concept
1. On the Hyperbridge chain, any account/module capable of dispatching an ISMP `PostRequest` (with `source` naturally set to the Hyperbridge state machine id since that's the true origin) crafts a request with `to = address(SimplexPaymaster)` and `body = abi.encodePacked(uint8(RequestKind.UpgradeContract), abi.encode(maliciousImpl, initData))`.
2. The request is relayed and delivered to `EvmHost`, which routes it to `SimplexPaymaster.onAccept` (per `IApp` dispatch), see: [3](#0-2) 
3. `onAccept` verifies only `incoming.request.source == hyperbridge()`, which is true since the request truly originated there — the `from` field, controlled by whichever account/module dispatched it, is never checked.
4. `ERC1967Utils.upgradeToAndCall(maliciousImpl, initData)` executes, swapping the paymaster's logic and running arbitrary attacker-supplied initialization code inside the paymaster's storage/fund context, enabling drainage of all held ERC-20/EntryPoint balances.

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L189-211)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
            revert UnauthorizedCall();
        }

        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        bytes calldata payload = incoming.request.body[1:];

        if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(payload, (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        } else if (kind == RequestKind.UpdateParams) {
            _setParams(abi.decode(payload, (Params)));
        } else if (kind == RequestKind.RegisterToken) {
            (address token, address oracle) = abi.decode(payload, (address, address));
            _registerToken(token, AggregatorV3Interface(oracle));
        } else if (kind == RequestKind.DeactivateToken) {
            _deactivateToken(abi.decode(payload, (address)));
        } else if (kind == RequestKind.WithdrawAssets) {
            (address token, uint256 amount) = abi.decode(payload, (address, uint256));
            _withdrawAssets(token, amount);
        }
    }
```

**File:** evm/src/core/HostManager.sol (L95-108)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
```
