## Title
Unprivileged direct token transfer permanently blocks `EvmHost.updateHostParams` fee-token migration - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.updateHostParamsInternal` refuses to change the configured `feeToken` while the host's `balanceOf(oldFeeToken)` is non-zero, but nothing prevents an unprivileged party from keeping that balance non-zero forever by repeatedly transferring a trivial amount of the old fee token directly to the host contract. This mirrors the Malt `LinearDistributor.declareReward` pattern: a state-changing/administrative code path derives a revert condition directly from `balanceOf(address(this))` instead of from an internally tracked accounting value, so anyone can grief it with a plain ERC-20 transfer, and there is no sweep mechanism to neutralize the griefed balance.

### Finding Description
`updateHostParamsInternal` (called only via the `hostManager`-gated `updateHostParams`, itself driven by cross-chain governance through `HostManager.onAccept`) contains: [1](#0-0) 

```
address oldFeeToken = feeToken();
if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
    uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
    if (balance != 0) revert CannotChangeFeeToken();
}
```

`balanceOf(address(this))` is the *actual* on-chain token balance, not an internally-tracked "declared" or "expected" amount. Any address can call `IERC20(oldFeeToken).transfer(hostAddress, 1)` at any time — no allowance, no permission, and no interaction with the host contract itself is required, since ERC-20 `transfer` is initiated by the sender, not the receiver. As soon as the balance is non-zero, every future `updateHostParams` call that tries to change `feeToken` reverts with `CannotChangeFeeToken`, exactly like Malt's `_forfeit` reverting once `balance > bufferRequirement` desynchronizes from `declaredBalance`.

Unlike `withdraw()` (also `hostManager`-gated), there is no `sweep`/drain step performed automatically before or during `updateHostParams`, and no way to zero the balance atomically with the parameter update in a single cross-chain governance message. `withdraw` and `updateHostParams` are delivered as two independent ISMP POST requests (`HostManager.onAccept` dispatches to `IHostManager.withdraw` or `IHostManager.updateHostParams` based on the decoded action byte): [2](#0-1) 

Because these are separate cross-chain messages relayed independently (potentially in different blocks), an attacker who observes a pending governance `withdraw`-then-`updateHostParams` sequence (or who simply monitors the mempool/relayer submissions) can re-send 1 wei of the old fee token to the host after any `withdraw` clears the balance and before the `updateHostParams` message lands, permanently re-triggering the revert.

### Impact Explanation
This blocks a legitimate cross-chain governance/host-management capability — migrating the fee token used for the whole ISMP fee-payment pipeline on that `EvmHost` — indefinitely, at negligible cost to the attacker (one ERC-20 transfer of dust value, repeatable forever). It does not directly steal funds, but it is a logic/DoS attack on host-management execution that the bounty scope explicitly includes ("Cross-chain admin or host-management effects" reachability issues), and it matches the exact bug class of the seed report: a `require`/`revert` gated on live `balanceOf()` rather than a tracked accounting variable, exploitable by anyone with no privileged role, relayer, or prover assumption.

### Likelihood Explanation
High likelihood of griefing viability, low likelihood of being noticed until governance actually attempts a fee-token change: any address holding (or able to acquire) a trivial amount of the current fee token can execute the griefing transfer with a single unauthenticated `transfer()` call to a publicly known contract address — no race condition beyond simple mempool monitoring is required, and the attack is trivially repeatable at negligible cost.

### Recommendation
Do not gate `feeToken` changes on the *live* `balanceOf(address(this))`. Instead:
- Track outstanding/expected fee-token liabilities internally (e.g., accrued but unclaimed relayer fees) and compare the change against that tracked value, or
- Perform an automatic sweep of the old fee token to a treasury/beneficiary as part of `updateHostParamsInternal` itself (atomic with the parameter change) rather than relying on a prior, separate `withdraw` governance message, or
- Simply drop the invariant and allow arbitrary residual dust of the old fee token to remain, since it poses no security risk on its own — the check exists only to avoid stranding meaningful balances, and a small non-zero balance should not indefinitely block governance.

### Proof of Concept
1. `EvmHost` is deployed with `feeToken = TokenA`.
2. Attacker (any EOA, no privilege) calls `TokenA.transfer(hostAddress, 1)`.
3. Hyperbridge governance later attempts to migrate the fee token via `HostManager.onAccept` → `updateHostParams(params)` with `params.feeToken = TokenB`.
4. `updateHostParamsInternal` computes `balance = IERC20(TokenA).balanceOf(address(this))`, which is now `≥ 1`, and reverts with `CannotChangeFeeToken`.
5. Even if governance first dispatches a `withdraw` message draining `TokenA` to zero, the attacker repeats step 2 before the follow-up `updateHostParams` message is delivered (these are two independent, non-atomic cross-chain ISMP messages), re-blocking the change indefinitely. [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/core/EvmHost.sol (L573-621)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }

    /**
     * @dev Updates the HostParams. Will reset all fishermen accounts and initialize any new state machines.
     * @param params, the new host params.
     */
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
