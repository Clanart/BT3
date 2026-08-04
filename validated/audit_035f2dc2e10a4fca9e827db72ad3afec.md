## Analysis

Reducing the seed report to its core primitive: **an admin/governance-only entrypoint accepts and executes attacker-influenceable parameters without validating the identity/authorization of the caller or content correctness**, leading to unintended fund movement or state change.

Searching Hyperbridge's cross-chain governance receivers for the analogous gap (input/origin validation missing on a privileged `onAccept` handler) surfaces a concrete discrepancy: `HostManager.onAccept` validates only the **chain-level** origin of an inbound governance message, but never validates the **module-level** origin (`request.from`), unlike the sibling `IntentGatewayV2.authenticate()` pattern which explicitly checks `request.from` against a known instance address.

### Title
Missing module-identity binding on `HostManager.onAccept` allows any Hyperbridge-chain module to trigger host withdraw/param-update governance actions - (`evm/src/core/HostManager.sol`)

### Summary
`HostManager.onAccept` is the sole gate that authorizes cross-chain governance actions (`Withdraw`, `SetHostParam`) against the local `EvmHost`. It checks that the inbound `PostRequest.source` equals the Hyperbridge state machine id, but never checks `PostRequest.from` (the specific module/pallet that dispatched the message). [1](#0-0)  This is inconsistent with the codebase's own established pattern in `IntentGatewayV2.authenticate()`, which requires `request.from` to match a specifically registered instance address before trusting message content, precisely to prevent any code running on the source chain from impersonating a privileged sender. [2](#0-1)  `BandwidthManager.onAccept` exhibits the same gap. [3](#0-2) 

### Finding Description
`HostManager` decodes the first body byte as an `OnAcceptActions` discriminant and, for `Withdraw`, forwards attacker/message-controlled `WithdrawParams` (arbitrary `beneficiary`, `token`, `amount`) straight into `EvmHost.withdraw`, and for `SetHostParam`, forwards an entire `HostParams` struct into `EvmHost.updateHostParams`: [1](#0-0) 

```
function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
    PostRequest calldata request = incoming.request;
    // Only the Hyperbridge parachain can send requests to this module.
    if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
    ...
}
```

The only authentication performed is `request.source == hyperbridge` (a **chain identifier**). The `from` field — which on Substrate identifies the specific pallet/account that issued the dispatch — is never inspected. `EvmHost.withdraw` itself performs a raw, unconditional transfer to whatever `beneficiary`/`amount` it is given: [4](#0-3)  and `EvmHost.updateHostParams` only performs structural sanity checks (non-zero addresses, interface support, non-empty hyperbridge id) — it does not, and cannot, verify that the *sender* of the governance message was actually the trusted governance module rather than an arbitrary pallet on the Hyperbridge chain. [5](#0-4) 

Compare this to `IntentGatewayV2`, where any state-changing inbound action (`RedeemEscrow`, `RefundEscrow`, and the hyperbridge-only actions `NewDeployment`/`UpdateParams`/`SweepDust`) is gated by `authenticate()`, which explicitly checks `request.from` against a specific, previously-registered contract address per source chain, not merely the chain id: [2](#0-1)  This shows the codebase's own security model treats "chain id matches" as insufficient authentication for privileged actions — `HostManager` (and `BandwidthManager`) do not apply that same standard to the two most consequential admin actions in the entire protocol: **draining bridge revenue** and **rewriting the host's consensus/handler/admin/fee-token configuration**.

### Impact Explanation
If any component on the Hyperbridge parachain other than the intended, singular trusted governance pallet is able to dispatch an ISMP `PostRequest` with `source = hyperbridge` and an arbitrary `to`/`body` (which is the generic, permissionless dispatch model used elsewhere in this same codebase — e.g. `BandwidthManager.purchase()` lets any EVM caller freely dispatch a `PostRequest` to an arbitrary pallet module id [6](#0-5) ), that message reaches `HostManager.onAccept` and is treated as fully authorized governance. This enables:
- Draining all accumulated host revenue (`feeToken`/native balance) to an attacker-chosen `beneficiary` via `Withdraw`.
- Rewriting `HostParams` — including `admin`, `handler`, `consensusClient`, `hostManager`, and `feeToken` — via `SetHostParam`, which can be used to install a malicious handler/consensus client and thereafter accept **false state commitments/proofs** for every future cross-chain message, i.e., false proof acceptance across the entire bridge.

This directly matches the required impact classes: unauthorized transaction/execution, transaction/logic manipulation, and (via a corrupted `consensusClient`/`handler`) false proof/state acceptance.

### Likelihood Explanation
Exploitability depends entirely on whether the Hyperbridge Substrate side restricts which pallet/account can dispatch a `PostRequest` whose `to` field targets a registered `HostManager`/host-manager module id — that permission model lives outside this EVM-side repository and could not be verified from local code. Within the EVM contracts alone, the missing `request.from` check is unambiguous and stands in clear contrast to the stricter pattern already used by `IntentGatewayV2`, indicating the check was omitted rather than deliberately unnecessary.

### Recommendation
Add an explicit check in `HostManager.onAccept` (and `BandwidthManager.onAccept`) that `request.from` equals a specifically configured, immutable governance-module identifier (analogous to `IntentGatewayV2`'s `instance()`/`authenticate()` pattern), in addition to the existing `request.source == hyperbridge` check, before executing `Withdraw` or `SetHostParam`.

### Proof of Concept
Conceptual reproduction (bounded by the info available in this repo):
1. Any entity capable of getting `pallet-ismp` (or any pallet with ISMP dispatch access) on the Hyperbridge chain to emit a `PostRequest{source: hyperbridge, to: <HostManager address>, from: <arbitrary/non-governance module id>, body: 0x00 || abi.encode(WithdrawParams{beneficiary: attacker, token: feeToken, amount: hostBalance})}` gets it relayed and proven through the standard ISMP handler.
2. `EvmHost` routes the proven request to `HostManager.onAccept`. [7](#0-6) 
3. The only check performed, `request.source.equals(hyperbridge)`, passes because `source` is the chain id, not the specific module.
4. `OnAcceptActions.Withdraw` is decoded and forwarded unconditionally to `IHostManager(_params.host).withdraw(withdrawParams)`. [8](#0-7) 
5. `EvmHost.withdraw` transfers the full requested `amount` to `attacker` with no further authorization check. [9](#0-8)

### Citations

**File:** evm/src/core/HostManager.sol (L95-109)
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
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L286-294)
```text
    /**
     * @dev Checks that the request originates from a known instance of the IntentGateway.
     */
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/BandwidthManager.sol (L165-181)
```text
        BandwidthPurchaseMsg memory body = BandwidthPurchaseMsg({
            app: app,
            tier: tier,
            months: months,
            chain: chain
        });

        commitment = IDispatcher(_host).dispatch(
            DispatchPost({
                dest: IDispatcher(_host).hyperbridge(),
                to: PALLET_BANDWIDTH_MODULE_ID,
                body: abi.encode(body),
                timeout: 0,
                fee: 0,
                payer: address(this)
            })
        );
```

**File:** evm/src/apps/BandwidthManager.sol (L201-211)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        PostRequest calldata request = incoming.request;

        if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.SetTiers) {
            Tier[] memory updates = abi.decode(request.body[1:], (Tier[]));
            for (uint256 i = 0; i < updates.length; i++) {
                tierPrice[updates[i].tier] = updates[i].price;
                emit TierSet(updates[i].tier, updates[i].price);
```

**File:** evm/src/core/EvmHost.sol (L581-645)
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

        if (
            params.handler == address(0) || address(params.handler).code.length == 0
                || !IERC165(params.handler).supportsInterface(type(IHandlerV2).interfaceId)
        ) {
            // otherwise cannot process new datagrams
            revert InvalidHandler();
        }

        if (
            params.consensusClient == address(0) || address(params.consensusClient).code.length == 0
                || !IERC165(params.consensusClient).supportsInterface(type(IConsensusV2).interfaceId)
        ) {
            // otherwise cannot process new consensus datagrams
            revert InvalidConsensusClient();
        }

        // otherwise cannot process new cross-chain governance requests
        if (keccak256(params.hyperbridge) == keccak256(bytes(""))) revert InvalidHyperbridgeId();

        // otherwise cannot process new datagrams
        uint256 stateMachinesLen = params.stateMachines.length;
        if (stateMachinesLen == 0) revert InvalidStateMachinesLength();

        // otherwise cannot process new datagrams
        if (1 days > params.unStakingPeriod) revert InvalidUnstakingPeriod();

        address oldFeeToken = feeToken();
        if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
            uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
            if (balance != 0) revert CannotChangeFeeToken();
        }

        // safe to emit here because invariants have already been checked
        // and don't want to store a temp variable for the old params
        emit HostParamsUpdated({oldParams: _hostParams, newParams: params});

        _hostParams.feeToken = params.feeToken;
        _hostParams.admin = params.admin;
        _hostParams.handler = params.handler;
        _hostParams.hostManager = params.hostManager;
        _hostParams.uniswapV2 = params.uniswapV2;
        _hostParams.unStakingPeriod = params.unStakingPeriod;
        _hostParams.challengePeriod = params.challengePeriod;
        _hostParams.consensusClient = params.consensusClient;
        _hostParams.stateMachines = params.stateMachines;
        _hostParams.hyperbridge = params.hyperbridge;

        // add whitelisted state machines
        for (uint256 i = 0; i < stateMachinesLen; ++i) {
            // create if it doesn't already exist
            if (_latestStateMachineHeight[params.stateMachines[i]] == 0) {
                _latestStateMachineHeight[params.stateMachines[i]] = 1;
            }
        }
    }
```

**File:** evm/src/core/EvmHost.sol (L647-660)
```text
    /**
     * @dev withdraws host revenue to the given address, can only be called by cross-chain governance
     * @param params, the parameters for withdrawal
     */
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
