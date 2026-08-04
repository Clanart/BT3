Confirmed: `IApp` interface (used for the ERC165 check) only declares `onAccept`/`onGetResponse`/`onPostRequestTimeout`/`onGetTimeout` — it has no `host()` method, so ERC165's `supportsInterface(type(IApp).interfaceId)` cannot and does not verify that a new `hostManager` actually binds back to this `EvmHost`.

### Title
`EvmHost.updateHostParams` accepts a new `hostManager` without verifying its `host()` binds back to this Host, permanently bricking governance/withdraw path - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.updateHostParamsInternal` validates a new `hostManager` only for non-zero address, having code, and implementing the `IApp` interface via ERC165 — the same shallow validation pattern flagged in the Y2K `VaultFactory.changeController` finding. It never checks that the candidate `hostManager` contract's own back-reference (`HostManager._params.host`) actually points to this `EvmHost` instance, mirroring the exact missing-reciprocal-check bug class from the seed report.

### Finding Description
`updateHostParamsInternal` performs these checks on the incoming `params.hostManager`: [1](#0-0) 

`IApp` (the interface checked via ERC165) exposes only the message callbacks, not `host()`: [2](#0-1) 

So any contract implementing `IApp` — regardless of what its own `host` field points to — passes the check. `HostManager` binds its `host` reference exactly once, out-of-band from `EvmHost`, via a separate admin call: [3](#0-2) 

Every privileged EvmHost action — `updateHostParams` and `withdraw` — is gated by `restrict(_hostParams.hostManager)`, i.e., it trusts whatever address is currently stored as `hostManager`: [4](#0-3) [5](#0-4) 

And `HostManager.onAccept` — the only path that can call back into `EvmHost.updateHostParams`/`withdraw` — is gated symmetrically by `restrict(_params.host)`, i.e., it trusts whatever address the manager itself recorded as `host`: [6](#0-5) 

If a `SetHostParam` governance message ever sets `params.hostManager` to a `HostManager` (or any other `IApp`-conforming contract) whose own `_params.host` is not this `EvmHost` — e.g., a freshly deployed manager not yet bound via `setIsmpHost`, or one mistakenly bound to a different host/chain deployment — the two one-directional trust checks stop matching each other. `EvmHost` now only accepts calls from the new manager, but the new manager's `onAccept` will never accept calls originating from this `EvmHost` (since its `_params.host` differs), so no future cross-chain `SetHostParam` or `Withdraw` message can ever reach this `EvmHost` again. This is the exact analog of the Y2K `VaultFactory.changeController` bug: a controlling reference is swapped without checking that the new controllee's own back-pointer matches, silently severing the control channel.

### Impact Explanation
Once the mismatched `hostManager` is set, `withdraw()` becomes permanently unreachable (governance can never again form a message that both the current `hostManager` and the `EvmHost`'s `restrict` modifiers will jointly accept), so protocol/relayer revenue accumulated in the host is irreversibly locked. `updateHostParams` is likewise permanently unreachable, so the host can never be reconfigured to recover (e.g., pointing to a working manager) except by redeploying — an unrecoverable state matching the "fund loss/lock" and "host-management effects reachable through wrong module bindings" categories in the impact gate.

### Likelihood Explanation
This requires only a single incorrect `hostManager` value in one legitimate `SetHostParam` governance dispatch (e.g., migrating to a newly deployed `HostManager` before its one-shot `setIsmpHost` call, or a copy-paste of the wrong chain's manager address) — there is no on-chain safeguard that would catch or revert such a mistake, unlike the analogous validation already present for `handler` and `consensusClient`, which check interface conformance but still lack the reciprocal-binding check that would actually prevent this specific class of misconfiguration.

### Recommendation
Extend `updateHostParamsInternal`'s validation of `params.hostManager` to also require `HostManager(params.hostManager).host() == address(this)` (or an equivalent explicit reciprocal-binding call) before accepting the new value, exactly as recommended in the seed report for `VaultFactory.changeController` (verify the callee's back-reference equals the caller before wiring it in).

### Proof of Concept
1. Deploy a fresh `HostManager2` for chain migration purposes but do not yet call `setIsmpHost` on it (so `_params.host == address(0)`), or call it pointed at a different `EvmHost` address by mistake.
2. Governance dispatches a `SetHostParam` PostRequest through the existing, correctly-bound `HostManager` with `HostParams.hostManager = address(HostManager2)`.
3. `EvmHost.updateHostParamsInternal` passes all checks — `HostManager2` has code and implements `IApp` — and stores `_hostParams.hostManager = address(HostManager2)`. [7](#0-6) 
4. Any subsequent governance `Withdraw` or `SetHostParam` message is routed by Hyperbridge to `HostManager2.onAccept`, which reverts because `msg.sender` (this `EvmHost`) does not equal `HostManager2._params.host` (`address(0)` or a different host).
5. `EvmHost.withdraw`/`updateHostParams` are now unreachable by any future governance message — funds/revenue in the host and the parameter-update path are permanently locked.

### Citations

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
```

**File:** evm/src/core/EvmHost.sol (L581-589)
```text
    function updateHostParamsInternal(HostParams memory params) internal {
        // check the params to prevent the host from getting bricked.
        if (
            params.hostManager == address(0) || address(params.hostManager).code.length == 0
                || !IERC165(params.hostManager).supportsInterface(type(IApp).interfaceId)
        ) {
            // otherwise cannot process new cross-chain governance requests
            revert InvalidHostManager();
        }
```

**File:** evm/src/core/EvmHost.sol (L627-631)
```text
        _hostParams.feeToken = params.feeToken;
        _hostParams.admin = params.admin;
        _hostParams.handler = params.handler;
        _hostParams.hostManager = params.hostManager;
        _hostParams.uniswapV2 = params.uniswapV2;
```

**File:** evm/src/core/EvmHost.sol (L651-660)
```text
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```

**File:** sdk/packages/core/contracts/interfaces/IApp.sol (L74-98)
```text
interface IApp {
    /**
     * @dev Called by the `Host` to notify an app of a new request the app may choose to respond immediately, or in a later block
     * @param incoming post request
     */
    function onAccept(IncomingPostRequest memory incoming) external;

    /**
     * @dev Called by the `Host` to notify an app of a get response to a previously sent out request
     * @param incoming get response
     */
    function onGetResponse(IncomingGetResponse memory incoming) external;

    /**
     * @dev Called by the `Host` to notify an app of post requests that were previously sent but have now timed-out
     * @param incoming post request timeout
     */
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external;

    /**
     * @dev Called by the `Host` to notify an app of get requests that were previously sent but have now timed-out
     * @param incoming get request timeout
     */
    function onGetTimeout(GetRequestTimeout memory incoming) external;
}
```

**File:** evm/src/core/HostManager.sol (L88-93)
```text
    // This function can only be called once by the admin to set the IsmpHost.
    // This exists to seal the cyclic dependency between this contract & the ismp host.
    function setIsmpHost(address hostAddr) public restrict(_params.admin) {
        _params.host = hostAddr;
        _params.admin = address(0);
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
