### Title
PREVRANDAO opcode returns a fully predictable value derived only from the block timestamp, not cryptographically secure randomness - ([File: x/evm/keeper/keeper.go])

### Summary
The Sei EVM computes the value returned by the `PREVRANDAO`/`DIFFICULTY` opcode (`block.prevrandao` in Solidity) as `Keccak256(blockTime.MarshalBinary())`, i.e. a hash of the block's own timestamp, rather than deriving it from an unpredictable, unbiasable entropy source (e.g. the RANDAO/RANDAOMIX beacon value used by post-merge Ethereum). This mirrors the underlying bug class of the referenced advisory (GHSA-768m-5w34-2xf5): a value that callers assume is cryptographically strong/unpredictable is in fact generated from low-entropy, attacker/validator-observable or -influenceable input.

### Finding Description
`Keeper.GetVMBlockContext` builds the `vm.BlockContext` passed into every EVM transaction/contract call. It sets the `Random` field (which the go-ethereum EVM exposes via the `PREVRANDAO` opcode, replacing `DIFFICULTY` after the merge) as follows: [1](#0-0) 

```go
// Use hash of block timestamp as info for PREVRANDAO
r, err := ctx.BlockHeader().Time.MarshalBinary()
...
rh := crypto.Keccak256Hash(r)
...
Random: &rh,
```

The identical pattern exists in the `giga` EVM keeper implementation: [2](#0-1) 

On real post-merge Ethereum, `PREVRANDAO` is the accumulated RANDAO mix — a value built up over many epochs of validator-submitted reveals that no single validator can predict or bias in advance, which is why Solidity/EVM developers widely (if imperfectly) treat `block.prevrandao` as a source of on-chain randomness (lotteries, raffles, NFT trait/reveal logic, game outcomes, matchmaking, etc.).

Here, Sei's `PREVRANDAO` is a deterministic function of only the current block's timestamp:
- The timestamp is chosen by the block proposer and is visible/near-fully-predictable to observers before the block executes (Tendermint block times increase monotonically and track wall-clock time within the consensus's block-time tolerance).
- There is no accumulated multi-block/multi-validator entropy contribution, no VRF, and no unpredictable per-block seed — the entire "randomness" is `keccak256(single_known_scalar)`.
- A block proposer (or anyone able to closely estimate the next block's timestamp, since intervals are short and consistent) can compute or grind the resulting hash before transactions in that block execute, and decide which transactions to include/reorder based on the resulting value.

This is directly analogous to the reported vulnerability class: a "randomness" primitive relied upon for security-sensitive decisions is generated with insufficient entropy/complexity, making its output predictable/forgeable to a party who should not be able to predict it.

### Impact Explanation
Any EVM contract deployed on Sei that follows the common (Ethereum-ecosystem-recommended) pattern of using `block.prevrandao` as an on-chain randomness source for financial outcomes (lotteries, raffles, gambling dApps, NFT mint/trait randomization, auction tie-breaks, etc.) inherits a randomness source that is far weaker than what the opcode implies and than what exists on Ethereum mainnet. Because the value is fully determined by a proposer-chosen, near-predictable timestamp with no accumulated entropy:
- A block proposer/validator can grind or select the block timestamp (within permitted consensus bounds) to produce a favorable `PREVRANDAO` outcome before finalizing which transactions to include, extracting value from prevrandao-dependent contracts (e.g., always winning a lottery, guaranteeing favorable NFT trait rolls).
- This constitutes fund loss/theft for the users of such contracts, transferring value to whichever party can predict/influence the outcome, entirely reachable through ordinary EVM transactions submitted by unprivileged users interacting with vulnerable contracts.

### Likelihood Explanation
Reliance on `block.prevrandao` (or its predecessor `block.difficulty`) for randomness is an extremely common pattern in EVM smart contracts, including many ports of existing Ethereum dApps to Sei's EVM. Given Sei markets full EVM compatibility, developers deploying pre-existing contracts (or new ones following widespread tutorials/patterns) will reasonably but incorrectly assume `PREVRANDAO` provides the same unpredictability guarantees as Ethereum mainnet's post-merge RANDAO. No special privilege is needed to exploit this beyond being a block proposer (a normal, expected validator role in the network) or being able to closely predict the near-term block timestamp.

### Recommendation
Do not derive `PREVRANDAO` solely from the current block's own timestamp. Instead:
- Base it on entropy that is fixed before the block proposer can choose the timestamp and is not fully controlled by any single party (e.g., derive from the previous block's finalized hash/vote data, or implement an actual multi-block accumulator/commit-reveal scheme analogous to Ethereum's RANDAO).
- At minimum, clearly document in developer-facing materials that Sei's `PREVRANDAO`/`block.difficulty` value is not a secure randomness source and should not be used for financial decisions, to avoid silently inheriting Ethereum ecosystem assumptions.

### Proof of Concept
1. Deploy a contract on Sei EVM that reads `block.prevrandao` (via `RANDOM`/`DIFFICULTY` opcode) to decide a payout, e.g. `if (uint256(block.prevrandao) % 2 == 0) payout();`.
2. A validator acting as block proposer observes the pending timestamp it is about to certify for the block (or an attacker closely estimates the next timestamp given Tendermint's tight block-time cadence), computes `keccak256(time.MarshalBinary())` off-chain for the candidate timestamp(s) as done in [3](#0-2) , and determines the resulting `PREVRANDAO` value before the block is finalized.
3. The proposer selectively includes/orders the randomness-dependent transaction only when the precomputed value is favorable (or an attacker submits/withholds their own qualifying transaction), guaranteeing a favorable outcome from the "randomness" check and extracting funds from the contract/other participants.

### Citations

**File:** x/evm/keeper/keeper.go (L274-313)
```go

	// Use hash of block timestamp as info for PREVRANDAO
	r, err := ctx.BlockHeader().Time.MarshalBinary()
	if err != nil {
		return nil, err
	}
	rh := crypto.Keccak256Hash(r)

	txfer := func(db vm.StateDB, sender, recipient common.Address, amount *uint256.Int) {
		if IsPayablePrecompile(&recipient) {
			state.TransferWithoutEvents(db, sender, recipient, amount)
		} else {
			core.Transfer(db, sender, recipient, amount)
		}
	}
	var baseFee *big.Int
	if ctx.ChainID() == Pacific1ChainID && ctx.BlockHeight() < 114945913 {
		baseFee = k.GetBaseFeePerGas(ctx).TruncateInt().BigInt()
	} else {
		baseFee = k.GetNextBaseFeePerGas(ctx).TruncateInt().BigInt()
	}

	return &vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    txfer,
		GetHash:     k.GetHashFn(ctx),
		Coinbase:    coinbase,
		GasLimit: func() uint64 {
			if ctx.ConsensusParams() != nil && ctx.ConsensusParams().Block != nil {
				return uint64(ctx.ConsensusParams().Block.MaxGas) //nolint:gosec
			}
			return DefaultBlockGasLimit
		}(),
		BlockNumber: big.NewInt(ctx.BlockHeight()),
		Time:        uint64(ctx.BlockHeader().Time.Unix()), //nolint:gosec
		Difficulty:  utils.Big0,                            // only needed for PoW
		BaseFee:     baseFee,
		BlobBaseFee: utils.Big1, // Cancun not enabled
		Random:      &rh,
	}, nil
```

**File:** giga/deps/xevm/keeper/keeper.go (L259-264)
```go
	// Use hash of block timestamp as info for PREVRANDAO
	r, err := ctx.BlockHeader().Time.MarshalBinary()
	if err != nil {
		return nil, err
	}
	rh := crypto.Keccak256Hash(r)
```
