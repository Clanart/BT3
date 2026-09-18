### Title
Unbounded delegation iteration in distribution precompile `rewards()` can exhaust transaction/query gas - (File: `precompiles/distribution/distribution.go`)

### Summary
The distribution precompile's `rewards` handler forwards directly to `DelegationTotalRewards`, which iterates over **every** delegation a delegator has made, with no pagination or limit, unlike the sibling staking precompile methods (`delegatorDelegations`, `delegatorUnbondingDelegations`, `delegatorValidators`) that all accept a `nextKey`/pagination argument.

### Finding Description
`PrecompileExecutor.rewards` in `precompiles/distribution/distribution.go` builds a `QueryDelegationTotalRewardsRequest` and calls `p.distrKeeper.DelegationTotalRewards` [1](#0-0) . That keeper method walks the delegator's entire delegation set via `k.stakingKeeper.IterateDelegations`, computing `DelegationRewardsForQuery` for every validator the address has ever delegated to, with no cap on the number of iterations and no pagination request or response [2](#0-1) .

This is unlike every other "list-of-many" delegator query exposed through the EVM precompiles: `delegatorDelegations`, `delegatorUnbondingDelegations`, and `delegatorValidators` on the staking precompile all thread a `nextKey`/`query.PageRequest` through to the keeper and cap what is returned per call [3](#0-2) [4](#0-3) . The distribution precompile's `rewards` and `delegatorValidators` methods were left without that same pagination discipline, so a delegator address that has delegated to a very large number of validators (which is itself unbounded — any account can call `delegate`/`redelegate` against many validators cheaply) causes this single precompile call to loop over all of them in one EVM call.

The precompile is registered as a `DynamicGasPrecompile`, and gas is metered from the real SDK gas meter (`pcommon.GetRemainingGas(ctx, p.evmKeeper)`) after the query executes rather than being priced up front proportional to expected work [5](#0-4) . This means the actual cost of the unbounded loop is only accounted for after it has already been paid for computationally by the node, and any caller (EVM tx sender or contract) invoking `rewards(address)` for an address with a large delegation set can force a disproportionately expensive computation relative to the flat/base gas charged for the call.

### Impact Explanation
Any externally-owned account or contract can call the `rewards` (and similarly the `delegatorValidators`) method of the distribution precompile at `0x0000000000000000000000000000000000001007` for an arbitrary address. If that address has spread delegations across a very large number of validators, the call becomes computationally heavy (unbounded loop over all delegations) — this can cause the call to revert with out-of-gas within the caller's own gas budget, or, if the caller intentionally sets a very high gas limit, consume disproportionate node CPU/time per call, degrading node responsiveness for a given block. This falls into the "resource-exhaustion"-adjacent DoS category the same as the original Covalent finding, reachable purely from unprivileged EVM transactions/precompile calls.

### Likelihood Explanation
Any account can cheaply create many small delegations to many different validators (bounded only by the number of validators in the active/total validator set, which on a live chain can be in the tens to low hundreds, and is not bounded by any per-account cap). This makes constructing a "wide" delegator with lots of delegation records straightforward and inexpensive for an attacker to set up, then repeatedly invoke `rewards(attackerAddress)` from an EVM contract or JSON-RPC `eth_call`/tx to trigger the expensive unbounded iteration.

### Recommendation
Apply the same pagination pattern already used elsewhere in the precompiles: expose a paginated distribution `rewards`/`delegatorValidators` precompile method that accepts and forwards a `nextKey` (as `delegatorDelegations` and `delegatorUnbondingDelegations` do in the staking precompile), and/or cap the number of delegations processed per call by pushing a bounded `maxRetrieve` style limit into `DelegationTotalRewards`/`DelegatorValidators` in the distribution keeper, consistent with `GetDelegatorDelegations(ctx, delegator, maxRetrieve)` already available in the staking keeper.

### Proof of Concept
1. From an unprivileged account, submit `MsgDelegate` (or the staking precompile `delegate` method) to a large number of distinct validators with minimal amounts, building up hundreds/thousands of delegation records for a single delegator address.
2. Call the distribution precompile at `0x...1007` method `rewards(delegatorAddress)` (or `delegatorValidators`) via `eth_call`/a transaction.
3. Observe that the call internally performs `IterateDelegations` over the full, unbounded delegation set [6](#0-5) , with cost growing linearly with the number of delegations and no pagination mechanism to cap it, unlike the equivalent staking precompile calls.

### Citations

**File:** precompiles/distribution/distribution.go (L558-578)
```go
	seiDelegatorAddress, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		rerr = err
		return
	}

	req := &distrtypes.QueryDelegationTotalRewardsRequest{
		DelegatorAddress: seiDelegatorAddress.String(),
	}

	wrappedC := sdk.WrapSDKContext(ctx)
	response, err := p.distrKeeper.DelegationTotalRewards(wrappedC, req)
	if err != nil {
		rerr = err
		return
	}

	rewardsOutput := getResponseOutput(response)
	ret, rerr = method.Outputs.Pack(rewardsOutput)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
```

**File:** sei-cosmos/x/distribution/keeper/grpc_query.go (L160-196)
```go
// DelegationTotalRewards the total rewards accrued by a each validator
func (k Keeper) DelegationTotalRewards(c context.Context, req *types.QueryDelegationTotalRewardsRequest) (*types.QueryDelegationTotalRewardsResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "invalid request")
	}

	if req.DelegatorAddress == "" {
		return nil, status.Error(codes.InvalidArgument, "empty delegator address")
	}

	ctx := sdk.UnwrapSDKContext(c)

	total := sdk.DecCoins{}
	var delRewards []types.DelegationDelegatorReward

	delAdr, err := sdk.AccAddressFromBech32(req.DelegatorAddress)
	if err != nil {
		return nil, err
	}

	k.stakingKeeper.IterateDelegations(
		ctx, delAdr,
		func(_ int64, del stakingtypes.DelegationI) (stop bool) {
			valAddr := del.GetValidatorAddr()
			val := k.stakingKeeper.Validator(ctx, valAddr)
			// Read-only for v6.7.0+; reproduces the pre-v6.7.0 period-increment write
			// only when re-tracing an older block (see DelegationRewardsForQuery).
			delReward := k.DelegationRewardsForQuery(ctx, val, del)

			delRewards = append(delRewards, types.NewDelegationDelegatorReward(valAddr, delReward))
			total = total.Add(delReward...)
			return false
		},
	)

	return &types.QueryDelegationTotalRewardsResponse{Rewards: delRewards, Total: total}, nil
}
```

**File:** precompiles/staking/staking.go (L1140-1165)
```go
func (p PrecompileExecutor) delegatorDelegations(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, 0, err
	}

	seiDelegatorAddress, err := pcommon.GetSeiAddressFromArg(ctx, args[0], p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}

	nextKey := args[1].([]byte)
	request := &stakingtypes.QueryDelegatorDelegationsRequest{
		DelegatorAddr: seiDelegatorAddress.String(),
		Pagination: &query.PageRequest{
			Key: nextKey,
		},
	}

	response, err := p.stakingQuerier.DelegatorDelegations(sdk.WrapSDKContext(ctx), request)
	if err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/staking/staking.go (L1344-1368)
```go
func (p PrecompileExecutor) delegatorValidators(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, 0, err
	}

	seiDelegatorAddress, err := pcommon.GetSeiAddressFromArg(ctx, args[0], p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}

	nextKey := args[1].([]byte)
	request := &stakingtypes.QueryDelegatorValidatorsRequest{
		DelegatorAddr: seiDelegatorAddress.String(),
		Pagination: &query.PageRequest{
			Key: nextKey,
		},
	}

	response, err := p.stakingQuerier.DelegatorValidators(sdk.WrapSDKContext(ctx), request)
	if err != nil {
		return nil, 0, err
```
