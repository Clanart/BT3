## Finding

### Title
`HyperbridgeLzEndpoint.send()` sets `payer: address(this)` with no withdrawal function, permanently locking refunded relayer fees and swap dust in the contract - (File: sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol)

### Summary
`HyperbridgeLzEndpoint.send()` dispatches every outbound message with `payer: address(this)` [1](#0-0) , i.e. the endpoint contract designates itself as the recipient of any relayer-fee refund. This is the same "refund-to-self" pattern as the audited Vault bug (`reportSnapshot` setting the LayerZero refund address to `payable(address(this))`). Unlike `EvmHost`, which exposes a governance-gated `withdraw(WithdrawParams)` to move native tokens or fee tokens out of the host [2](#0-1) , `HyperbridgeLzEndpoint` has no equivalent function anywhere in its ~700 lines. There is no `withdraw`, `sweep`, `rescue`, or `onlyOwner` native/ERC20 recovery entry point in the contract.

### Finding Description
`send()` builds a `DispatchPost` whose `payer` field is `address(this)` and forwards `msg.value` to `IDispatcher(_host).dispatch{value: msg.value}(request)` [3](#0-2) . The `payer` in ISMP's `DispatchPost`/`FeeMetadata` model is the account credited when a relayer fee is refunded (on timeout, or when excess native value is swapped and any residual/overpayment is returned) — the same semantic role as the LayerZero `refundAddress` in the original bug report. Because this endpoint hard-codes `payer = address(this)` instead of forwarding the OApp's actual caller (the `_refundAddress` parameter received in `send()` is explicitly discarded — see the commented-out `/* _refundAddress */` parameter at line 264), any native token that flows back to the payer settles into `HyperbridgeLzEndpoint`'s own balance rather than to the OApp/user who originally paid.

The `quote()` function documents this exact expectation: "Excess native is refunded by the uniswap router" [4](#0-3)  — confirming that overpaid native ETH (the 2x buffer quoted for exactly this purpose) is expected to return somewhere, and since `payer = address(this)`, it returns to the endpoint contract itself.

Once ETH accumulates in the endpoint, there is no code path to move it out:
- No `withdraw`/`rescue` function exists anywhere in the file.
- `Ownable`/`onlyOwner` is used only for `setHost`, `setRelayerFee`, `setDefaultRelayerFee`, `setEidMapping`, `pause`, `unpause` — none of which touch the contract's native balance.
- The contract has no `receive()`/fallback with recovery logic either; any ETH landing here (via timeout refunds, swap dust, or accidental direct sends) is permanently stuck, exactly mirroring the "Vault" bug: funds that can only be used for future transactions but which nobody can withdraw for the rightful beneficiary.

This differs from the mitigation actually shipped by the team elsewhere in this same codebase — `EvmHost.withdraw()` — which is properly access-controlled via `restrict(_hostParams.hostManager)` and can only be triggered by governance through `HostManager.onAccept` after validating `request.source == hyperbridge` [5](#0-4) . `HyperbridgeLzEndpoint` implements none of this — it is a standalone per-OApp adapter deployed by any integrator (per the docs, "deployed per OApp, per chain"), and has zero mechanism to recover native funds that land in it as fee-refund residue.

### Impact Explanation
Every OApp/OFT that routes through `HyperbridgeLzEndpoint` loses any refunded relayer-fee overpayment or swap-dust ETH permanently — the funds are neither returned to the original caller (because `payer` is hard-coded to the endpoint, not the caller) nor recoverable by governance/owner (no withdrawal function exists). Given `quote()` deliberately quotes a 2x buffer above the real relayer fee specifically so it can absorb legacy per-byte protocol fees, a portion of every native-fee-paying `send()` call is expected to be refunded — and 100% of that refund is stranded. This is a direct, protocol-wide loss of user/OApp funds, matching the bounty's "stealing or loss of funds" category.

### Likelihood Explanation
High. This triggers on the ordinary, unprivileged happy path — every `send()` call paid in native ETH via `msg.value` (the documented normal flow, no malicious actor, relayer, or governance action required). No permissioned or adversarial precondition is needed; it is simply structural: the refund target is wrong and there is no recovery function.

### Recommendation
1. Forward the caller-supplied `_refundAddress` (currently discarded) as the `payer` in `DispatchPost`, so fee refunds/swap dust return to the actual OApp/user rather than to the endpoint contract.
2. Add an owner-gated (or otherwise access-controlled) `withdraw(address token, address to, uint256 amount)` function to `HyperbridgeLzEndpoint` — mirroring `EvmHost.withdraw()` — to allow recovery of any native ETH or ERC20 fee tokens that end up stuck in the contract, whether from this bug or from stray direct transfers.

### Proof of Concept
1. Deploy `HyperbridgeLzEndpoint`, call `setHost()` to wire it to an `EvmHost` and configure an EID mapping.
2. An OApp calls `endpoint.send{value: X}(params, refundAddress)` where `X` is deliberately higher than the exact relayer fee required (as `quote()` itself recommends via its 2x buffer).
3. `send()` builds `DispatchPost{ payer: address(this), fee: relayerFee(dstEid), ... }` and forwards `msg.value` to `IDispatcher(_host).dispatch{value: msg.value}(request)` [6](#0-5) .
4. When the host swaps the excess native token or refunds unused fee (e.g., on a request timeout, or via the Uniswap swap path referenced in `quote()`'s comment), the refund settles to `payer == address(this)` — i.e., into `HyperbridgeLzEndpoint`'s own ETH balance, not to `refundAddress` or the OApp.
5. Attempt to call any function on `HyperbridgeLzEndpoint` to retrieve that ETH: `grep` of the full contract source confirms no `withdraw`, `rescue`, `sweep`, or payable-recovery function exists — the ETH is permanently locked in the contract.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L287-294)
```text
        DispatchPost memory request = DispatchPost({
            dest: dest,
            to: abi.encodePacked(address(this)),
            body: body,
            timeout: 0,
            fee: relayerFee(_params.dstEid),
            payer: address(this)
        });
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L296-306)
```text
        if (msg.value > 0) {
            IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
            // Fee tokens already transferred to this contract by OFT's _payLzToken.
            // The quoted lzTokenFee includes a buffer above the relayer fee so the
            // legacy deployed host's per-byte protocol fee can be paid out of it;
            // approve our full feeToken balance and let the host take what it needs.
            address feeToken = IDispatcher(_host).feeToken();
            IERC20(feeToken).forceApprove(_host, IERC20(feeToken).balanceOf(address(this)));
            IDispatcher(_host).dispatch(request);
        }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L337-340)
```text
        // Apply a generous 2x buffer to absorb the legacy deployed host's
        // per-byte protocol fee (the in-source host has no such markup). Excess
        // native is refunded by the uniswap router; excess feeToken approval is
        // simply unused.
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

**File:** evm/src/core/HostManager.sol (L95-104)
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
```
