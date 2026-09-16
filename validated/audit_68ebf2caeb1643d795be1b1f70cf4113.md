This confirms the analog. The `governance.governingnode` role (single-vote authority, analogous to the "owner" in the report) can cast a `reward.mintingamount` vote with `FormatChecker: noopFormatChecker` and no consistency check, allowing an unbounded inflation value to be ratified into the chain's governance parameters and directly consumed by the reward engine to mint tokens every block. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Title
Unbounded `reward.mintingamount` governance parameter allows the governing node to mint unlimited KAIA every block - (File: kaiax/gov/param.go)

### Summary
The governance parameter `reward.mintingamount` (`RewardMintingAmount`) has no upper-bound validation anywhere in the vote-verification pipeline. In `single` governance mode (used on Kaia Mainnet and Kairos), the sole authorized voter — the `governingnode` — can set this value to an arbitrarily large number via the `governance_vote` API. Once ratified at the epoch boundary, this value is fed directly into the block reward calculation and minted every block for the entire following epoch, with no cap, multisig requirement, or sanity check. This is the direct on-chain analog of the reported "owner can set unlimited amount" issue in the Cumulative Merkle Drop contracts, where a privileged single-key role could set an unbounded value with no independent verification, and the recommended mitigation (multisig) is absent here as well — a single EOA fully controls token minting rate.

### Finding Description
The `RewardMintingAmount` parameter definition in `kaiax/gov/param.go` uses `FormatChecker: noopFormatChecker`, meaning any well-formed big integer is accepted with no range or sanity restriction: [1](#0-0) 

`checkConsistency`, which is the only cross-parameter validation hook invoked during both vote submission (`governance_vote` API) and header vote verification (`VerifyVote`), explicitly whitelists `RewardMintingAmount` among a group of parameters that receive no additional consistency checks beyond the format check performed in `NewVoteData()`: [2](#0-1) 

The vote is submitted through the `governance_vote` RPC (`Vote` method on `headerGovAPI`), which only checks that the caller is the designated `governingnode` in single mode — it performs no magnitude/sanity check on the value itself: [3](#0-2) 

At the epoch boundary, `VerifyVote` (called during header/vote verification for every node) re-validates only voter identity/authorization and delegates the same unchecked `checkConsistency` path: [4](#0-3) 

Once ratified into `header.Governance` and picked up by `GetParamSet`, the value populates `RewardConfig.MintingAmount`, which is used verbatim as the "minted" reward source in every block's reward computation for the entire subsequent epoch (e.g., `getDeferredRewardFullKore`): [5](#0-4) 

This mirrors the reported vulnerability class: a single privileged signer ("owner"/governing node) can commit an unbounded numeric value that is later trusted and executed to move/create value, with no additional signer, quorum, or bound enforcing sane limits — exactly the scenario the original report flags and for which it recommends multisig control.

### Impact Explanation
If the `governingnode` key is compromised, misconfigured, or misused, an attacker/malicious operator can set `reward.mintingamount` to an extremely large value (e.g., `2^128`). Once ratified at the next epoch boundary, every subsequent block for the whole epoch (up to `epoch` blocks, e.g., one week on Mainnet) would mint that inflated amount and distribute it among the proposer, stakers, and funds — a direct, massive, and continuous supply inflation of the native KAIA token. This causes catastrophic, irreversible economic damage (currency debasement) affecting every holder on the network, satisfying the "supply inflation" acceptance criterion.

### Likelihood Explanation
`single` governance mode is the mode actually used on Kaia Mainnet and Kairos testnet per the module's own documentation, so this is not a theoretical configuration. The path requires control of the single `governingnode` key (no code bug required to reach it — this is a design gap: no bound/consistency check exists for this specific parameter, unlike `kip71.lowerboundbasefee`/`kip71.upperboundbasefee`, which do have explicit bound checks in `checkConsistency`). Given that the governing node is a single EOA (not enforced to be multisig at the protocol layer), any single-key compromise or insider action directly yields this outcome without needing any other actor's cooperation.

### Recommendation
Add an explicit `FormatChecker`/consistency check for `RewardMintingAmount` (and any other unbounded reward/governance value) that enforces a reasonable maximum (e.g., relative to historical minting amount or a hardcoded ceiling), similar to the existing bound checks for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`. Additionally, consider requiring multi-party approval (e.g., migrating governance to `none` mode with quorum-based ratification, or requiring multiple independent signers) for changes to economically critical parameters such as minting amount, rather than relying on a single governing node key.

### Proof of Concept
1. Deploy/operate a Kaia network in `single` governance mode with a known `governingnode` address (as is the case on Mainnet/Kairos).
2. As the `governingnode`, call the RPC:
   ```
   curl "http://localhost:8551" -X POST -H 'Content-Type: application/json' --data '
     {"jsonrpc":"2.0","id":1,"method":"governance_vote","params":[
       "reward.mintingamount",
       "340282366920938463463374607431768211455"
     ]}'
   ```
   This passes `Vote()` in `kaiax/gov/headergov/impl/api.go` since only voter-identity is checked, not magnitude.
3. When the node becomes proposer, the vote is written into `header.Vote`; `VerifyVote` on all other nodes accepts it because `checkConsistency` performs no bound check for `RewardMintingAmount` (see the whitelist in `header.go:214-219`).
4. At the next epoch boundary the vote is ratified into `header.Governance`.
5. Starting the following epoch, `RewardConfig.MintingAmount` is populated with the attacker-chosen huge value and is minted every block via `getDeferredRewardFullKore`/`getDeferredRewardFullLegacy`/`getDeferredRewardSimple`, inflating the KAIA supply by that amount per block for the entire epoch.

### Citations

**File:** kaiax/gov/param.go (L414-424)
```go
	RewardMintingAmount: {
		Canonicalizer: bigIntCanonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.Reward == nil || c.Governance.Reward.MintingAmount == nil {
				return nil, errors.New("reward is not set")
			}
			return c.Governance.Reward.MintingAmount, nil
		},
		DefaultValue: big.NewInt(0),
	},
```

**File:** kaiax/gov/headergov/impl/header.go (L101-107)
```go
	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}
```

**File:** kaiax/gov/headergov/impl/header.go (L213-220)
```go
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```

**File:** kaiax/gov/headergov/impl/api.go (L53-63)
```go
func (api *headerGovAPI) Vote(name string, value any) (string, error) {
	var (
		voter     = api.h.nodeAddress
		nextBlock = api.h.Chain.CurrentBlock().NumberU64() + 1
		gp        = api.h.GetParamSet(nextBlock)
		gMode     = gp.GovernanceMode
	)

	if gMode == "single" && voter != gp.GoverningNode {
		return "", ErrVotePermissionDenied
	}
```

**File:** kaiax/reward/impl/getter.go (L336-339)
```go
	var (
		spec             = reward.NewRewardSpec()
		minted           = new(big.Int).Set(config.MintingAmount)
		distributableFee = new(big.Int).Sub(execFee, burntFee)
```
