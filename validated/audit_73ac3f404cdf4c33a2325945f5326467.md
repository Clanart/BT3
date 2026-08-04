### Title
`HostManager.setIsmpHost()` doesn't check that `hostAddr` isn't zero, permanently bricking cross-chain governance and locking bridge revenue - ([File: evm/src/core/HostManager.sol])

### Summary
`HostManager.setIsmpHost()` is a one-shot function that sets the `_params.host` address and unconditionally zeroes out `_params.admin` in the same call, with no validation that `hostAddr != address(0)` (or that it is even the correct `EvmHost` address). This mirrors the pants `MainToken.set_mint_multisig()` bug class: a privileged setter for a critical address with no zero-address check, whose mistake can never be corrected because the only privileged caller is burned in the same transaction.

### Finding Description
`setIsmpHost` is restricted to `_params.admin` and is meant to be called exactly once to seal the cyclic dependency between `HostManager` and `EvmHost`: [1](#0-0) 

There is no check that `hostAddr != address(0)` or that it actually matches the intended `EvmHost`. Immediately after the assignment, `_params.admin` is set to `address(0)` regardless of whether `hostAddr` was valid. Since the `restrict` modifier requires `msg.sender == caller`, and no externally-controlled account can ever be `address(0)`, this call is genuinely irreversible: [2](#0-1) 

`_params.host` subsequently gates every privileged action on the contract — both the getter used for outbound authentication (`host()`) and the inbound governance entrypoint `onAccept`, which is restricted to `msg.sender == _params.host`: [3](#0-2) 

If the admin calls `setIsmpHost(address(0))` by mistake (or with any address other than the real `EvmHost`), `_params.host` becomes permanently wrong, `_params.admin` is permanently zero, and there is no other function in the contract capable of correcting `_params.host`.

### Impact Explanation
`onAccept` is the sole channel through which Hyperbridge governance delivers `Withdraw` and `SetHostParam` instructions to a chain's `EvmHost`: [4](#0-3) 

Once `_params.host` is bricked, the real `EvmHost` (whose `_hostParams.hostManager` still points at this `HostManager` instance) can never successfully call `onAccept`, because `msg.sender` (the real host) will never equal the corrupted `_params.host`. This permanently locks the `withdraw()` path on `EvmHost` — restricted to `restrict(_hostParams.hostManager)`, i.e., only reachable via this `HostManager`'s `onAccept` — so all fee-token/native revenue accrued by the host becomes permanently unwithdrawable, and `updateHostParams` can never again be pushed through cross-chain governance for that chain. This is a genuine, unrecoverable loss/lock of bridged funds and loss of host-management capability, matching the bounty's fund-loss and host-management categories.

### Likelihood Explanation
This requires the legitimate `admin` (a privileged, non-attacker actor) to make a single mistaken call — exactly the same trust assumption as the original pants finding, which was also triggered by an authorized operator mis-invoking a privileged setter. No malicious peer, relayer, or prover is required; the bug is purely the missing input validation on a one-shot, self-destructive setter, which is realistic during deployment/bootstrapping when `HostManager` and `EvmHost` addresses are wired together.

### Recommendation
Add a zero-address (and ideally interface/code-length) check to `setIsmpHost` before committing the value and burning `_params.admin`, mirroring the validation already applied to `hostManager`, `handler`, and `consensusClient` in `EvmHost.updateHostParamsInternal`:

```solidity
function setIsmpHost(address hostAddr) public restrict(_params.admin) {
    if (hostAddr == address(0) || hostAddr.code.length == 0) revert InvalidHost();
    _params.host = hostAddr;
    _params.admin = address(0);
}
```

### Proof of Concept
1. Deploy `HostManager` with `HostManagerParams{ admin: deployer, host: address(0) }`.
2. `deployer` calls `setIsmpHost(address(0))` (typo/misconfiguration) — the call succeeds since there is no zero check.
3. `_params.host` is now `address(0)` and `_params.admin` is now `address(0)`.
4. The real `EvmHost` (configured with `hostManager = address(thisHostManager)`) later attempts to deliver a `Withdraw`/`SetHostParam` governance message via `HostManager.onAccept`; the call reverts with `UnauthorizedAction` because `msg.sender` (the real host) never equals `_params.host` (`address(0)`), and no account can call `setIsmpHost` again to fix it since `_params.admin == address(0)`.
5. `EvmHost.withdraw()` and `EvmHost.updateHostParams()` — both gated by `restrict(_hostParams.hostManager)` and only reachable through this bricked `onAccept` path — become permanently unreachable, freezing accrued host revenue. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/core/HostManager.sol (L56-60)
```text
    // @dev restricts call to the provided `caller`
    modifier restrict(address caller) {
        if (msg.sender != caller) revert UnauthorizedAction();
        _;
    }
```

**File:** evm/src/core/HostManager.sol (L83-108)
```text
    // Implementation of HyperApp's required host() function
    function host() public view override returns (address) {
        return _params.host;
    }

    // This function can only be called once by the admin to set the IsmpHost.
    // This exists to seal the cyclic dependency between this contract & the ismp host.
    function setIsmpHost(address hostAddr) public restrict(_params.admin) {
        _params.host = hostAddr;
        _params.admin = address(0);
    }

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

**File:** evm/src/core/EvmHost.sol (L573-576)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
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
