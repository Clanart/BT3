### Title
Overly-broad `MsgSubmitProposal` authz grant lets a gov proposal-authorization grantee drain the granter's account via an arbitrary self-chosen deposit - ([File: precompiles/gov/gov.go])

### Summary
`precompiles/gov/gov.go` exposes `grantProposalAuthorization`, which grants a `GenericAuthorization` scoped only to the `MsgSubmitProposal` message type [1](#0-0) . `GenericAuthorization` in Cosmos SDK's `x/authz` only checks the message type URL — it places no constraint on message field values such as the deposit amount. A grantee can therefore build its own `MsgSubmitProposal` with an initial deposit equal to the granter's *entire* balance and any proposal content, and execute it through `authztypes.MsgExec`, debiting the granter's account for that amount [2](#0-1) . This mirrors the reported bug class: a role intended for a narrow purpose (open/close a trade with fixed semantics vs. "submit proposals") is instead reachable with fully attacker-controlled parameters (deposit amount, proposal content), allowing it to move/burn funds well beyond the intended scope.

### Finding Description
`grantProposalAuthorization` creates a native authz grant of type `GenericAuthorization("/cosmos.gov.v1beta1.MsgSubmitProposal")` from `granter` to `grantee` [3](#0-2) , using `GrantGenericAuthorizations` → `authztypes.NewGenericAuthorization(...)` [4](#0-3) .

The precompile's own `submitProposalWithAuthorization` path attempts to bound the deposit by tying it to `msg.value` sent by the grantee through `prepareSubmitProposal`/`HandlePaymentUsei` [5](#0-4) [6](#0-5) . However, the underlying native `x/authz` grant is not restricted to being invoked only through this EVM precompile path — the Solidity interface explicitly documents that the raw grant can also be exercised via a native Cosmos `MsgExec`, where the initial deposit is fully attacker-chosen and debited straight from the granter, bypassing the precompile's `msg.value`-based bound entirely [7](#0-6) . `ExecuteAuthorization`/native `MsgExec` simply validates the message and forwards it to the authz module, with no additional amount check [2](#0-1) . The project's own test suite exercises exactly this "native MsgExec" flow and confirms the granter's balance is directly reduced by an amount chosen inside the constructed `MsgSubmitProposal`, not by any value the granter approved via `msg.value` [8](#0-7) .

### Impact Explanation
Any account that is granted `grantProposalAuthorization` — intended only to let a trusted assistant submit proposals on the user's behalf — can instead submit a proposal with an initial deposit equal to the user's entire spendable balance. If the resulting proposal fails to pass (e.g., it never reaches quorum, or is deliberately made to fail), the deposit is burned/forfeited per governance module rules, resulting in a permanent, unrecoverable loss of the granter's funds initiated entirely by the grantee, with parameters (deposit amount, proposal content) chosen unilaterally by the grantee and unconstrained by the authorization itself. This is functionally the same failure mode as the reported issue: a permission meant to enable a narrow "on behalf of" action is broad enough to let the delegate move/destroy the principal's funds via attacker-controlled call parameters.

### Likelihood Explanation
Reaching this requires only: (1) a Sei account granting `grantProposalAuthorization` to another EVM address — a normal, documented feature explicitly marketed for automating governance participation — and (2) the grantee issuing a single `MsgExec` transaction. No special privilege, consensus assumption, or validator collusion is needed; it is directly reachable by any two unprivileged accounts exchanging a grant, matching the "unprivileged transaction sender" analog criteria. The Solidity NatSpec comment already flags this ("Grant it only to a fully trusted account; proposal deposits can be permanently lost"), indicating the maintainers are aware of the risk but have not enforced an on-chain limit (e.g., spend-limit-style authorization) comparable to `SendAuthorization`'s `spend_limit`.

### Recommendation
Do not rely on `GenericAuthorization` for `MsgSubmitProposal`. Introduce a dedicated, bounded authorization type (analogous to `x/bank`'s `SendAuthorization`) that caps the maximum deposit amount the grantee may commit on the granter's behalf, and/or restrict the granted authorization's applicability to being consumable only through the EVM precompile path where `msg.value` provides an explicit, per-call bound. At minimum, document and consider enforcing a hard cap (e.g., percentage of balance or fixed max) at grant time.

### Proof of Concept
1. `granter` (Sei address `A`) calls `GOV_CONTRACT.grantProposalAuthorization(granteeAddr, expiration)`, creating a `GenericAuthorization("/cosmos.gov.v1beta1.MsgSubmitProposal")` grant from `A` to `grantee` [3](#0-2) .
2. `grantee` constructs `MsgSubmitProposal{Proposer: A, InitialDeposit: A's_full_balance, Content: <benign-looking text proposal>}` and wraps it in `authztypes.MsgExec{Grantee: grantee, Msgs: [msg]}` — this is the exact same flow validated in the repo's own test at [9](#0-8) , but with the deposit set to the granter's entire balance instead of a small test amount.
3. `grantee` submits the `MsgExec` directly to the chain (bypassing the EVM precompile's `msg.value` bound entirely) or via `authzMsgServer.Exec` [2](#0-1) .
4. `A`'s account is immediately debited for the full deposit amount, transferred to the gov module.
5. If the proposal is left to fail (or `grantee` crafts it so it will), the deposit is burned per gov module deposit-handling rules, permanently destroying `A`'s funds without `A`'s further consent.

### Citations

**File:** precompiles/gov/legacy/v67/gov.go (L217-223)
```go
func (p PrecompileExecutor) grantVoteAuthorization(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	return p.grantAuthorization(ctx, method, caller, args, value, &govtypes.MsgVote{})
}

func (p PrecompileExecutor) grantProposalAuthorization(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	return p.grantAuthorization(ctx, method, caller, args, value, &govtypes.MsgSubmitProposal{})
}
```

**File:** precompiles/gov/legacy/v67/gov.go (L526-560)
```go
// prepareSubmitProposal builds the exact Cosmos message used by both direct
// and authorized submission, so the two entry points cannot drift in parsing,
// payment, or proposal-content behavior.
func (p PrecompileExecutor) prepareSubmitProposal(ctx sdk.Context, proposer sdk.AccAddress, proposalJSON string, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM) (*govtypes.MsgSubmitProposal, error) {
	var proposal Proposal
	if err := json.Unmarshal([]byte(proposalJSON), &proposal); err != nil {
		return nil, fmt.Errorf("failed to parse proposal JSON: %w", err)
	}

	initialDeposit, err := pcommon.HandlePaymentUsei(
		ctx,
		p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address),
		proposer,
		value,
		p.bankKeeper,
		p.evmKeeper,
		hooks,
		evm.GetDepth(),
	)
	if err != nil {
		return nil, err
	}

	content, err := p.createProposalContent(ctx, proposal)
	if err != nil {
		return nil, err
	}
	msg, err := govtypes.NewMsgSubmitProposalWithExpedite(content, sdk.NewCoins(initialDeposit), proposer, proposal.IsExpedited)
	if err != nil {
		return nil, err
	}
	if err := msg.ValidateBasic(); err != nil {
		return nil, err
	}
	return msg, nil
```

**File:** precompiles/common/authorization.go (L15-31)
```go
// GrantGenericAuthorizations creates one scoped grant for each message type.
// Cosmos authz keys grants by message type, so a single EVM-facing permission
// that covers several actions must be represented by several native grants.
func GrantGenericAuthorizations(
	ctx sdk.Context,
	msgServer utils.AuthzMsgServer,
	granter sdk.AccAddress,
	grantee sdk.AccAddress,
	expiration time.Time,
	authorizedMsgs ...sdk.Msg,
) error {
	authorizations := make([]authztypes.Authorization, len(authorizedMsgs))
	for i, authorizedMsg := range authorizedMsgs {
		authorizations[i] = authztypes.NewGenericAuthorization(sdk.MsgTypeURL(authorizedMsg))
	}
	return GrantAuthorizations(ctx, msgServer, granter, grantee, expiration, authorizations...)
}
```

**File:** precompiles/common/authorization.go (L77-88)
```go
// ExecuteAuthorization routes a concrete message through the native authz
// server, preserving its message-type scope and normal grant consumption.
func ExecuteAuthorization(ctx sdk.Context, msgServer utils.AuthzMsgServer, grantee sdk.AccAddress, msg sdk.Msg) (*authztypes.MsgExecResponse, error) {
	if err := msg.ValidateBasic(); err != nil {
		return nil, err
	}
	exec := authztypes.NewMsgExec(grantee, []sdk.Msg{msg})
	if err := exec.ValidateBasic(); err != nil {
		return nil, err
	}
	return msgServer.Exec(sdk.WrapSDKContext(ctx), &exec)
}
```

**File:** precompiles/gov/gov.go (L487-521)
```go
func (p PrecompileExecutor) submitProposalWithAuthorization(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, 0, err
	}

	grantee, err := pcommon.GetSeiAddressByEvmAddress(ctx, caller, p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	proposer, err := pcommon.GetSeiAddressFromArg(ctx, args[0], p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	msg, err := p.prepareSubmitProposal(ctx, proposer, args[1].(string), value, hooks, evm)
	if err != nil {
		return nil, 0, err
	}

	execRes, err := pcommon.ExecuteAuthorization(ctx, p.authzMsgServer, grantee, msg)
	if err != nil {
		return nil, 0, err
	}
	if len(execRes.Results) != 1 {
		return nil, 0, fmt.Errorf("expected one submit proposal authorization result, got %d", len(execRes.Results))
	}
	var submitRes govtypes.MsgSubmitProposalResponse
	if err := submitRes.Unmarshal(execRes.Results[0]); err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(submitRes.ProposalId)
	if err != nil {
		return nil, 0, err
	}
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), nil
```

**File:** precompiles/gov/Gov.sol (L94-104)
```text
    /**
     * @dev Grant an account permission to submit proposals on behalf of the caller
     * @param grantee The account receiving the proposal authorization
     * @param expiration Unix timestamp after which the authorization is invalid
     * @return success Whether the authorization was successfully granted
     * @notice This native MsgSubmitProposal authorization can also be used through Cosmos MsgExec with an arbitrary initial deposit debited from the caller. Grant it only to a fully trusted account; proposal deposits can be permanently lost
     */
    function grantProposalAuthorization(
        address grantee,
        int64 expiration
    ) external returns (bool success);
```

**File:** precompiles/gov/gov_test.go (L451-472)
```go
	nativeProposalDeposit := sdk.NewCoins(sdk.NewCoin(k.GetBaseDenom(statedb.Ctx()), sdk.NewInt(25)))
	require.NoError(t, k.BankKeeper().MintCoins(statedb.Ctx(), evmtypes.ModuleName, nativeProposalDeposit))
	require.NoError(t, k.BankKeeper().SendCoinsFromModuleToAccount(statedb.Ctx(), evmtypes.ModuleName, granterSeiAddr, nativeProposalDeposit))
	nativeProposalID, err := testApp.GovKeeper.GetProposalID(statedb.Ctx())
	require.NoError(t, err)
	nativeProposalContent := govtypes.ContentFromProposalType(
		"native authorized proposal",
		"submitted through native MsgExec",
		govtypes.ProposalTypeText,
		false,
	)
	nativeProposalMsg, err := govtypes.NewMsgSubmitProposal(nativeProposalContent, nativeProposalDeposit, granterSeiAddr)
	require.NoError(t, err)
	nativeExec := authztypes.NewMsgExec(granteeSeiAddr, []sdk.Msg{nativeProposalMsg})
	granterBalanceBefore := k.BankKeeper().GetBalance(statedb.Ctx(), granterSeiAddr, k.GetBaseDenom(statedb.Ctx()))
	_, err = testApp.AuthzKeeper.Exec(sdk.WrapSDKContext(statedb.Ctx()), &nativeExec)
	require.NoError(t, err)
	granterBalanceAfter := k.BankKeeper().GetBalance(statedb.Ctx(), granterSeiAddr, k.GetBaseDenom(statedb.Ctx()))
	require.True(t, granterBalanceBefore.Amount.Sub(nativeProposalDeposit.AmountOf(k.GetBaseDenom(statedb.Ctx()))).Equal(granterBalanceAfter.Amount))
	nativeProposal, found := testApp.GovKeeper.GetProposal(statedb.Ctx(), nativeProposalID)
	require.True(t, found)
	require.Equal(t, nativeProposalDeposit, nativeProposal.TotalDeposit)
```
