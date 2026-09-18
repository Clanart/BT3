### Title
Predictable EVM `PREVRANDAO` derived from block timestamp enables RNG manipulation by block proposers/attackers - (File: `x/evm/keeper/keeper.go`)

### Summary
Sei's EVM `GetVMBlockContext` sets the `PREVRANDAO` value (`block.difficulty`/`block.prevrandao` opcode) to `keccak256(block timestamp)` instead of real per-block unpredictable entropy. Since block timestamps on Sei are chosen by the block proposer under Tendermint's Proposer-Based Timestamp (PBTS) rules and are known/observable before finalization, any EVM contract deployed on Sei that uses `block.prevrandao` for on-chain randomness (lotteries, blind boxes, raffles, RNG-based games — exactly the ATMBlindBox bug class) inherits a fully predictable "random" seed, exactly analogous to the fallback path exploited in the ATMBlindBox incident.

### Finding Description
In `GetVMBlockContext`, the value exposed to the EVM as `PREVRANDAO` is computed purely from the block header timestamp: [1](#0-0) 

```go
// Use hash of block timestamp as info for PREVRANDAO
r, err := ctx.BlockHeader().Time.MarshalBinary()
...
rh := crypto.Keccak256Hash(r)
...
Random: &rh,
```

Unlike post-Merge Ethereum, where `PREVRANDAO` is the output of the beacon chain's RANDAO accumulator (unpredictable until the block is proposed, and contributed to by many independent validators over an epoch), Sei substitutes a deterministic hash of the block's `Time` field — a value that is:

1. Chosen unilaterally by the current block proposer (subject only to Tendermint's PBTS "timely" window bounds), giving the proposer significant latitude to select a timestamp of their choosing.
2. Broadcast in the `Proposal` message before the block is finalized/committed, at which point any observer (not only the proposer) can compute `keccak256(timestamp)` and know exactly what `block.prevrandao` will resolve to for that block — before it is executed.
3. Reused identically for every EVM transaction/contract in the same block, with no additional per-transaction entropy.

This mirrors the ATMBlindBox root cause: `keccak256(block.prevrandao, betId, block.timestamp)` was evaluable by the attacker prior to settlement because the underlying entropy source was weak/predictable rather than because of a bug in the specific contract. On Sei, the chain itself supplies the weak entropy source to every deployed contract that reads `block.prevrandao` — the vulnerability is baked into the base layer, not just an application bug.

### Impact Explanation
Any smart contract deployed on Sei EVM that relies on `block.prevrandao` (or `block.difficulty`, its Solidity alias) as a randomness source for financial outcomes — lotteries, blind boxes, gambling, gacha/loot mechanics, giveaway selection — is exposed to fund-loss exploitation: an attacker (or the block proposer, who fully controls the timestamp within the PBTS acceptance window) can predict the resolved "random" value before submitting the transaction that consumes it, and selectively bet/settle only when the outcome favors them, draining the contract exactly as happened to ATMBlindBox (~99,000 USD lost). Because Sei markets itself as EVM-compatible for existing Solidity dApps, contracts ported from other EVM chains using `prevrandao`-based RNG patterns are silently exposed to this weaker guarantee without any code change or warning.

### Likelihood Explanation
High for any contract using this pattern: the entropy source is fully deterministic once the block timestamp is known, and the timestamp is proposer-chosen and observable via the `Proposal` message ahead of block commitment; no privileged access is required — only submitting an ordinary EVM transaction (e.g., a bet-settlement call) after computing the predictable outcome, exactly like the reproduced ATMBlindBox exploit path.

### Recommendation
Do not derive `PREVRANDAO` from the block timestamp. If no true per-block validator-contributed entropy source (e.g., a VRF-based commit/reveal or a Tendermint-native unpredictable value not controllable by the proposer) is available, document explicitly that `block.prevrandao`/`block.difficulty` MUST NOT be used for randomness on Sei, and consider exposing a dedicated verifiable-randomness precompile instead so dApp developers are not led into false security assumptions carried over from post-Merge Ethereum semantics.

### Proof of Concept
Conceptually mirroring the ATMBlindBox PoC:
1. An attacker (or colluding proposer) observes the `Proposal` message for the next block, which contains the chosen timestamp.
2. Attacker computes `keccak256(timestamp)` (the exact value `x/evm/keeper/keeper.go:280` will set as `PREVRANDAO`/`block.prevrandao` for that block).
3. Attacker deploys/interacts with any contract that resolves a bet, mint, or reward using `block.prevrandao` in that block, submitting the transaction only when the precomputed outcome favors them (e.g., `uint256(prevrandao) % 16` yields a winning digit), draining the contract's payout pool — the same fund-loss mechanism as the ATMBlindBox incident, just triggered via Sei's chain-level RNG substitution instead of a contract-level fallback bug.

### Citations

**File:** x/evm/keeper/keeper.go (L275-313)
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
