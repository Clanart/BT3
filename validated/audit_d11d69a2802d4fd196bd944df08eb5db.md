### Title
`EvmHost.updateHostParamsInternal` allows `feeToken` to be set to the zero address, silently breaking fee collection and relayer payouts across the entire host - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.updateHostParamsInternal` explicitly zero-address/code-checks `hostManager`, `handler`, and `consensusClient` before accepting new `HostParams`, but performs **no equivalent check on `feeToken`**. The only feeToken-related guard, `CannotChangeFeeToken`, only fires when the *old* feeToken is non-zero and still holds a balance — it does nothing to prevent the *new* feeToken from being `address(0)`. This is the same broken invariant as the AlgebraFactory `vaultAddress` report: a single governance-settable address, consumed unconditionally by fund-moving code throughout the contract, has no zero-address guard at the point it's written.

### Finding Description
`updateHostParamsInternal` performs targeted validation for several params but omits `feeToken`: [1](#0-0) 

Specifically:
- `hostManager` is checked for `address(0)`, code presence, and ERC165 interface support.
- `handler` and `consensusClient` receive the same treatment.
- `feeToken` only triggers `CannotChangeFeeToken` if `oldFeeToken != address(0) && oldFeeToken != params.feeToken && balance != 0` — i.e., it protects against *losing funds already held in the old token*, not against setting the *new* token to zero.

`feeToken()` is read directly from `_hostParams` and used as the ERC-20 that funds relayer fees, request/response dispatch fees, and revenue withdrawals across the host: [2](#0-1) 

`updateHostParams` is only reachable via the `hostManager` contract, and `HostManager.onAccept` forwards `SetHostParam` payloads originating from cross-chain governance requests dispatched by the Hyperbridge parachain, without any additional bounds-checking on the decoded `HostParams`: [3](#0-2) 

Once `feeToken` is `address(0)`, `withdraw()` reinterprets the token field as "native ETH" and pays out with a raw `.call{value: amount}`, since it branches purely on `params.token == address(0)`: [4](#0-3) 

This overloads the same sentinel value (`address(0)`) used elsewhere in the codebase to mean "native token" (see `IntentGatewayV2.withdraw`'s identical pattern) with the "ERC20 fee token" role, so once `feeToken` collapses to zero, every downstream fee-charging path that calls `IERC20(feeToken()).safeTransferFrom(...)` targets a non-contract address. A low-level call to an address with no code trivially succeeds with empty returndata, so `SafeERC20` treats the transfer as successful even though **no tokens actually move** — the dispatcher believes relayer/protocol fees were collected when they were not.

### Impact Explanation
If `feeToken` is ever driven to `address(0)` via `updateHostParams`, every POST/GET request dispatched afterward records a relayer fee in `_requestCommitments`/events without any real ERC-20 ever being pulled from the requester, since the "transfer" against a codeless address silently no-ops. This breaks the entire fee accounting invariant of the host:
- Relayers relay requests believing fees are escrowed, but the host never actually held the tokens, so subsequent `withdraw()` calls for those "collected" fees will fail or drain unrelated balances.
- Because `feeToken()` is also consumed by `BandwidthManager`, `IntentGatewayV2`, and `IntentsBase` (per the earlier grep) as the canonical protocol fee token, downstream apps built on the same host inherit the same broken/zero fee token, similarly bypassing fee enforcement or misrouting transfers.

This matches the "bridged assets/relayer rewards/bandwidth balances must move exactly once and only to the rightful beneficiary and amount" pivot: fee funds are effectively lost/never collected, and relayer reward accounting becomes decoupled from real token custody.

### Likelihood Explanation
Reaching this state requires the cross-chain `HostManager`/governance path to submit a `SetHostParam` update with `feeToken = address(0)` — comparable to the original report's precondition that "an admin sets the failure state." No malicious peer, prover, or leaked key is required beyond the same governance channel that already legitimately updates all other host params; the code path itself contains no safeguard analogous to the ones already present for `hostManager`/`handler`/`consensusClient`, so a single missing validation line is what stands between normal operation and a protocol-wide silent fee bypass.

### Recommendation
Add the same zero-address/code-presence validation used for `hostManager`, `handler`, and `consensusClient` to `feeToken` inside `updateHostParamsInternal`, e.g. `if (params.feeToken == address(0) || address(params.feeToken).code.length == 0) revert InvalidFeeToken();`. Additionally, avoid overloading `address(0)` to mean both "native token" (in `withdraw`) and "unset fee token," to prevent similar confusion elsewhere in the codebase.

### Proof of Concept
1. Cross-chain governance (via `HostManager.onAccept` → `OnAcceptActions.SetHostParam`) submits a `HostParams` update where every other field is valid but `feeToken = address(0)`.
2. `EvmHost.updateHostParamsInternal` passes all existing checks (`hostManager`, `handler`, `consensusClient`, `hyperbridge` id, `stateMachines`, `unStakingPeriod`) because none of them reference `feeToken`, and the `CannotChangeFeeToken` guard is skipped whenever the old fee-token balance is zero (e.g., right after governance drains it via `withdraw`).
3. `_hostParams.feeToken` becomes `address(0)`.
4. Any subsequent dispatch path that does `IERC20(feeToken()).safeTransferFrom(msg.sender, address(this), fee)` executes a call to a codeless address, which succeeds trivially without moving any tokens, yet the host records the fee as collected in `_requestCommitments`/emits it in `PostRequestEvent`/`GetRequestEvent`.
5. Relayers and the protocol now believe fees exist that were never actually escrowed, breaking withdrawal accounting for everyone relying on the host's fee token going forward.

### Citations

**File:** evm/src/core/EvmHost.sol (L461-466)
```text
    /**
     * @return the address of the ERC-20 fee token contract on this state machine
     */
    function feeToken() public view returns (address) {
        return _hostParams.feeToken;
    }
```

**File:** evm/src/core/EvmHost.sol (L581-621)
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
```

**File:** evm/src/core/EvmHost.sol (L651-659)
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
