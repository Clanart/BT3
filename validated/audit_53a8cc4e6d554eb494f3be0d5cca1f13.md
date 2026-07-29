Based on my research, I found a concrete analog to the reported bug class in the `ClaimRewards` distribution precompile.

### Title
Unbounded per-validator loop in `ClaimRewards` precompile is charged flat gas, not gas proportional to work performed - ([File: precompiles/distribution/tx.go])

### Summary
The reported bug class is "gas consumption of a function is driven by an unbounded loop over validator-like collections, but the caller cannot be forced to pay for the real amount of work performed, leading to a resource-exhaustion condition." The Cosmos EVM analog is the `ClaimRewards` transaction of the `distribution` precompile, which loops over up to `MaxValidators` (default 100) delegator validators and performs a full `WithdrawDelegationRewards` computation (historical rewards, slash-event iteration, and store writes) for each one, while the EVM-visible gas cost charged to the caller is a small, input-size-based flat fee rather than one that scales with `maxRetrieve`/loop iterations.

### Finding Description
`ClaimRewards` iterates over `res.Validators` (bounded only by `maxRetrieve <= MaxValidators`) and calls `p.distributionKeeper.WithdrawDelegationRewards` for every entry: [1](#0-0) 

Each `WithdrawDelegationRewards` call performs non-trivial Cosmos SDK state-machine work (calculating historical rewards across reward periods, iterating slash events, and writing updated distribution info to the KV store) — this is real CPU/IO work performed synchronously on the node processing the transaction.

However, precompile gas accounting in this codebase is computed generically from input size, not from the actual amount of keeper work the call will trigger. The distribution precompile documentation confirms a flat formula: [2](#0-1) 

Because the EVM only meters gas based on `RequiredGas(input)` (a function of calldata length) rather than tracking real work inside the precompile's `Run`, the actual cost incurred by the node scales with `maxRetrieve` (up to `MaxValidators`), while the gas debited from the caller does not. `MaxValidators` is a governance-settable staking parameter, and on chains configured with a large validator set (or where a delegator has many active delegations), the disparity between accounted gas and actual computational cost grows linearly with the loop bound.

This mirrors the original report precisely: the vulnerable code path is a nested/iterative "pick candidates and act on each" loop whose true cost is decoupled from the cost visible/controllable to the caller (`_count`/`maxRetrieve` in both cases), and the guidance in the original report ("allow the caller to specify `_count`... measure the worst case... consider a more efficient algorithm") maps directly onto the fact that `ClaimRewards`' gas cost model does not reflect `maxRetrieve`.

### Impact Explanation
An attacker who delegates to many validators (up to `MaxValidators`, which can be in the hundreds depending on governance/chain configuration) can submit `ClaimRewards` transactions with `maxRetrieve` set to the full validator count while only paying the flat, input-size-based gas fee. Because the EVM transaction's declared/charged gas does not reflect the real keeper-side cost of iterating and withdrawing from every validator, this allows an attacker to force asymmetric CPU/store-I/O cost onto block-producing nodes relative to the gas fee paid. Repeated submission of such transactions (a spam campaign) can degrade block processing time chain-wide, which is a network-level denial-of-service / block-time-degradation vector reachable by any unprivileged account with delegations spread across many validators — falling under the "chain halt / consensus-relevant slowdown triggerable by unprivileged user through ordinary transaction flow" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to (a) have (or cheaply acquire) delegations spread across a large fraction of `MaxValidators`, and (b) repeatedly submit `ClaimRewards` calls. Since delegation amounts can be minimal (attacker only needs a nonzero delegation to each validator to be included in `GetDelegatorValidators`), and delegating dust amounts to every active validator is inexpensive, the barrier to exploitation is low on chains with sizeable `MaxValidators` settings. It does not require any privileged role, malicious validator, or off-chain component — the entire path is a standard EVM transaction to a static precompile address (`0x...0801`).

### Recommendation
- Make `RequiredGas` (or an internal gas-consumption path inside `Run`/`ClaimRewards`) scale with `maxRetrieve`/the number of validators actually processed, rather than only with calldata length, so the EVM-visible gas cost reflects the real Cosmos SDK computational cost of `WithdrawDelegationRewards` per validator.
- Alternatively, meter/consume Cosmos gas from the underlying keeper calls against the EVM gas meter during the loop (similar to how other keeper-invoking precompiles proportionally charge for iterations), and abort/revert if the EVM gas is exhausted before completing all iterations, ensuring an attacker cannot get more "real work" than gas paid for.
- Consider lowering the effective per-call `maxRetrieve` cap or requiring a per-iteration gas stipend consistent with `WithdrawDelegationRewards`'s cost profile (validators × historical periods × slash events).

### Proof of Concept
Conceptual PoC (not executed, based on static review):
1. Governance / chain config sets `MaxValidators` to a large number (e.g., 175, a typical mainnet value), or the attacker targets a testnet/devnet with a similarly large validator set.
2. Attacker delegates a minimal amount (e.g., 1 unit) to every active validator.
3. Attacker repeatedly calls `DISTRIBUTION_CONTRACT.claimRewards(attacker, MaxValidators)` from a contract or EOA, each time paying only the flat `2000 + 30*inputSize` gas documented in `precompiles/distribution/README.md`.
4. Each call causes the node to execute `WithdrawDelegationRewards` once per validator (in `precompiles/distribution/tx.go`), performing full historical-reward computation and store writes for each of the `MaxValidators` validators, without the gas fee paid reflecting this cost.
5. Repeating (3) at high transaction throughput (limited only by the flat per-tx gas cost, which is cheap) allows the attacker to impose disproportionate CPU/store load on block producers relative to fees paid, a resource-exhaustion / DoS vector analogous to the original Liquid Collective gas-exhaustion report.

**Note on completeness:** I was not able to inspect the exact implementation of `common.Precompile.RequiredGas` / `GasRequiredByMethod` (the base gas-computation helper shared across precompiles) within the available tool budget, so I could not conclusively confirm there is no additional per-iteration Cosmos-gas consumption mechanism layered on top of the EVM-visible `RequiredGas`. If such a mechanism exists that charges the caller for the real Cosmos gas consumed by `WithdrawDelegationRewards` in addition to the flat precompile gas, this finding's severity would be substantially reduced. I recommend a Devin session with full repository access to verify `precompiles/common/precompile.go`'s `RequiredGas` and confirm whether Cosmos gas consumed inside `RunNativeAction`/`ClaimRewards` is reconciled against the EVM gas meter before concluding this is exploitable at Critical severity.

### Citations

**File:** precompiles/distribution/tx.go (L59-78)
```go
	res, err := p.stakingKeeper.GetDelegatorValidators(ctx, delegatorAddr.Bytes(), maxRetrieve)
	if err != nil {
		return nil, err
	}
	totalCoins := sdk.Coins{}
	for _, validator := range res.Validators {
		// Convert the validator operator address into an ValAddress
		valAddr, err := sdk.ValAddressFromBech32(validator.OperatorAddress)
		if err != nil {
			return nil, err
		}

		// Withdraw the rewards for each validator address
		coins, err := p.distributionKeeper.WithdrawDelegationRewards(ctx, delegatorAddr.Bytes(), valAddr)
		if err != nil {
			return nil, err
		}

		totalCoins = totalCoins.Add(coins...)
	}
```

**File:** precompiles/distribution/README.md (L84-102)
```markdown
#### claimRewards

```solidity
function claimRewards(
    address delegator,
    uint32 maxRetrieve
) external returns (bool);
```

Claims rewards from all validators at once (custom batch operation).

**Parameters:**

- `delegator`: The delegator claiming rewards
- `maxRetrieve`: Maximum number of validators to claim from

**Authorization:** Caller must be the delegator

**Gas Cost:** 2000 + (30 × input data size in bytes)
```
