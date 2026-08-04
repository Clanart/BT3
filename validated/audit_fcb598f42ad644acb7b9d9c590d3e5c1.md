This confirms the core issue: `HostManager.onAccept` in `evm/src/core/HostManager.sol` authorizes governance actions (fund withdrawal and host-parameter updates) based solely on the source **chain id** (`request.source == hyperbridge()`), never checking `request.from` (the sending module/pallet address on that chain). The test suite itself demonstrates this — `test_host_manager_withdraw` and `test_host_manager_set_host_params` in `evm/tests/rust/src/tests/host_manager.rs` construct a `PostRequest` with an arbitrary `from: env.sender.as_slice().to_vec()` (any account) and it is accepted as long as `source == StateMachine::Kusama(2000)`, draining fee tokens or rewriting `HostParams`.

### Title
Cross-chain governance withdraw/param-update accepted from any module on the Hyperbridge chain, not just the authorized governance module - (File: evm/src/core/HostManager.sol)

### Summary
`HostManager.onAccept` mirrors the reported "hidden governance" pattern: two authority checks exist in the protocol (chain-level `source` binding and module-level `from` binding), but only the weaker one is enforced. This is the same class of bug as the VUSD dual-governance report — a second, unenforced authorization layer creates a bypassable trust boundary that is not obvious from the code's stated intent ("Only the Hyperbridge parachain can send requests to this module").

### Finding Description
`HostManager.onAccept` in [1](#0-0)  only checks:
```solidity
if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
It never inspects `request.from`, the field that identifies which specific pallet/contract on the source chain dispatched the message. Once the chain-id check passes, the decoded `action` byte drives either an unrestricted `Withdraw` (drains the host's revenue to an attacker-chosen `beneficiary`) or `SetHostParam` (overwrites `admin`, `handler`, `hostManager`, `consensusClient`, etc.) via [2](#0-1)  and [3](#0-2) , both of which are gated only by `restrict(_hostParams.hostManager)` — i.e., they trust whatever `HostManager.onAccept` decided to forward.

This is documented as a known pattern elsewhere in the codebase for comparison: the docs explicitly note for the sibling `BandwidthManager` contract that the *inbound* direction *does* check the module id (`request.from` must equal the registered `BandwidthManager<T>` address), while the *outbound* direction (pallet→manager, i.e. the same direction as `HostManager`) is stated to have "no module-id lookup" — see [4](#0-3) . `HostManager.sol` follows the same weaker "chain-only" model.

The unit tests confirm this is exploitable with an arbitrary sender: `from: env.sender.as_slice().to_vec()` (an arbitrary account, not any specific whitelisted pallet address) is accepted by `onAccept` purely because `source: StateMachine::Kusama(2000)` matches, letting the test successfully withdraw funds and rewrite `HostParams` — see [5](#0-4)  and [6](#0-5) .

Practically, this means any account or pallet capable of dispatching an ISMP POST request from the Hyperbridge parachain to the target EVM chain (with `to` set to the `HostManager` address) can forge governance-class instructions, provided nothing on the Substrate side restricts which pallet/account may originate a POST with an arbitrary `to`. Whether a permissionless-dispatch path to arbitrary `to` addresses actually exists on the Hyperbridge parachain (e.g., through a demo/test pallet, XCM-triggered dispatch, or a generic ISMP dispatch extrinsic open to normal accounts) could not be fully confirmed within the scope of this review — this determines whether the missing `from` check is remotely reachable by an unprivileged actor or only reachable by already-privileged pallets.

### Impact Explanation
If reachable by an unprivileged sender, this allows unauthorized withdrawal of all accumulated bridge revenue (fee tokens/native ETH) to an attacker-chosen beneficiary, and/or a full rewrite of `HostParams` (including `admin`, `handler`, `consensusClient`, `hostManager` itself), which is a "cross-chain admin/host-management effect reachable through wrong module binding" per the stated bounty pivots — a critical fund-loss and false-authority-acceptance issue.

### Likelihood Explanation
Likelihood depends entirely on whether any account other than the intended governance pallet can cause a POST request to be dispatched from the Hyperbridge parachain with an arbitrary `to` (targeting the remote `HostManager`) and arbitrary `body`. This repository review found the EVM-side check is definitively missing the module (`from`) binding, matching the exact pattern the bandwidth docs call out as intentionally present on one side and absent on the other. Confirming exploitability further requires verifying Substrate-side dispatch-origin restrictions, which was not fully traceable in the available index.

### Recommendation
Add a `from`/module-id check in `HostManager.onAccept` (and any equivalent host-manager contracts, e.g. Tron's variant) analogous to the check the docs describe for `BandwidthManager`'s inbound path: store the authorized governance module id on the Hyperbridge chain in `HostManagerParams` and require `request.from.equals(_params.governanceModuleId)` in addition to the `source` chain check, so that only the single intended governance pallet — not any account/pallet on the source chain — can trigger `Withdraw` or `SetHostParam`.

### Proof of Concept
Using the existing test harness pattern in `evm/tests/rust/src/tests/host_manager.rs`:
1. Construct a `PostRequest` with `source: StateMachine::Kusama(2000)` (the correct Hyperbridge chain id) but `from` set to an arbitrary, non-governance account address.
2. Set `body = [ACTION_WITHDRAW] || abi.encode(WithdrawParams{ beneficiary: attacker, amount: host.balance, token: feeToken })`.
3. Call `HostManager.onAccept` as the host (satisfying `restrict(_params.host)`), exactly as `test_host_manager_withdraw` does at [7](#0-6) .
4. Observe funds are withdrawn to the attacker's beneficiary despite the request never having originated from the actual governance pallet — the only check performed is the chain id, not the sender module.

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

**File:** evm/src/core/EvmHost.sol (L573-575)
```text
    function updateHostParams(HostParams memory params) external virtual restrict(_hostParams.hostManager) {
        updateHostParamsInternal(params);
    }
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

**File:** docs/content/developers/evm/bandwidth/governance.mdx (L14-18)
```text
Two flows, two trust models:

- **Pallet → manager** (outbound governance). `BandwidthManager.onAccept` checks `request.source == IDispatcher(_host).hyperbridge()`. Only messages dispatched from Hyperbridge are honored. The `to` field is the manager's address — no module-id lookup.
- **Manager → pallet** (inbound purchases). The pallet rejects any purchase whose `request.from` doesn't equal the address stored under `BandwidthManager<T>::get(request.source)`. An attacker who deploys their own contract on a source chain cannot mint subscriptions.

```

**File:** evm/tests/rust/src/tests/host_manager.rs (L82-108)
```rust
	// Build a withdraw request (body = [0] + abi.encode(WithdrawParams)).
	// Withdraw the fee token (non-zero `token`) — the zero address would be
	// the native-ETH path which this test isn't exercising.
	let params = WithdrawalParams {
		beneficiary_address: H160::random().as_bytes().to_vec(),
		amount: SubstrateU256::from(500_000_000_000_000_000_000u128),
		token: H160::from_slice(env.fee_token.as_slice()),
	};

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body: params.abi_encode().expect("20-byte beneficiary"),
	};
	let evm_request: EvmPostRequest = post.into();

	// HostManager.onAccept is `restrict(_params.host)` — must call AS the host
	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	env.call_as(host_addr, manager, calldata);

	let withdraw_amount = U256::from(500u128) * U256::from(10u128.pow(18));
	assert_eq!(host_balance(&mut env), amount_to_mint - withdraw_amount);
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L196-213)
```rust

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body,
	};
	let evm_request: EvmPostRequest = post.into();

	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	env.call_as(host_addr, manager, calldata);

	let updated = host_params(&mut env);
	assert_eq!(updated.challengePeriod, new_challenge_period);
```
