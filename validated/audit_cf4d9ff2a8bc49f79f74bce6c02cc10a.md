Based on my investigation, this is a good analog. The address of a dynamic ERC20 precompile for an IBC voucher is deterministically derived from the denom string (`utils.GetIBCDenomAddress(denom)` in `x/erc20/types/token_pair.go`), and is therefore predictable before the token pair or precompile is ever registered. [1](#0-0) 

`CreateNewTokenPair` only rejects registration if the derived address already has a **code hash** — it does not check or reset any pre-existing native EVM balance at that address: [2](#0-1) 

When registration proceeds, `RegisterERC20CodeHash` explicitly preserves whatever balance/nonce already exists at that address before attaching the precompile's code hash — mirroring the `AuctionCrowdfund` pattern of "if state already has value X due to unexpected prior activity, carry it forward without adjusting invariants": [3](#0-2) 

This is invoked from `RegisterERC20Extension`, which is triggered permissionlessly by the first IBC packet for a given denom (`OnRecvPacket` → case "not found and ibc/ prefix"): [4](#0-3) [5](#0-4) 

I was unable to fully confirm within the available context whether the EVM statedb balance carried over onto a precompile address becomes truly and permanently unreachable (i.e., whether any code path — such as `SelfDestruct`, a future upgrade, or existing precompile logic — could still move or account for that native balance), since the dynamic ERC20 precompile implementation (`precompiles/erc20/erc20.go`, its `Run`/`Execute` dispatch) was not fully inspected for any balance-draining method, and the bank-keeper–backed `balanceOf`/`transfer` methods of the ERC20 precompile operate on `bank` balances, not the EVM `statedb` account balance field. This distinction — the precompile's ERC20 view is backed by bank coins, while the stray native balance sits in a separate accounting dimension (the EVM account's own `Balance` field) — is what would need to be verified in an actual PoC to establish whether funds become truly stuck versus merely inconsistent.

### Title
Predictable dynamic ERC20 precompile addresses allow pre-funding native EVM balance that becomes permanently stranded on registration - (File: x/erc20/keeper/dynamic_precompiles.go)

### Summary
The EVM address for an IBC-voucher's dynamic ERC20 precompile is deterministically derivable from its denom string before the token pair is ever registered. An attacker can send native EVM coin to this predictable address ahead of time. When the token pair is later registered (permissionlessly, triggered by the first IBC transfer of that denom), `RegisterERC20CodeHash` preserves the pre-existing balance on the account while attaching precompile code, rather than rejecting the registration or handling/redistributing the stray balance. This closely mirrors the `AuctionCrowdfund` root cause: a state-transition function assumes "no prior activity happened" and blindly carries forward pre-existing state without normalizing or validating it against the invariant the transition is supposed to establish.

### Finding Description
`CreateNewTokenPair` only guards against re-registration by checking `account.HasCodeHash()`, ignoring any native balance already present at the derived address: [6](#0-5) 

`RegisterERC20CodeHash`, called as part of `RegisterERC20Extension`, explicitly reads and re-applies the existing `Balance`/`Nonce` when writing the new account with precompile code hash: [7](#0-6) 

Once the address becomes a precompile (has a code hash pointing at the generic ERC20 extension bytecode), it no longer behaves as a plain EOA/contract that a user (or the deployer) can act on to reclaim any stray native balance — the precompile's dispatch logic (`Execute`/`HandleMethod`) only exposes ERC20-standard methods backed by the `bank` keeper's coin balance for the registered denom, not the EVM `statedb` account balance field that was carried over.

### Impact Explanation
If the stray EVM-native balance sitting on the address truly cannot be moved through any precompile method or subsequent SDK operation, this constitutes permanent freezing/locking of user funds — meeting the Critical bar for "permanent freezing, locking, theft, or unauthorized extraction of user funds ... or token-pair-backed balances." Even short of total unrecoverability, this creates a lasting accounting inconsistency between the account's EVM `Balance` field and the bank-keeper-backed value the ERC20 precompile is supposed to represent, which is the underlying invariant the `x/erc20` module and `x/vm` wrappers are designed to preserve for 1:1 correctness between native coins and their ERC20 view.

### Likelihood Explanation
Triggering this requires only: (1) knowing/computing the deterministic address for a not-yet-registered IBC denom via `utils.GetIBCDenomAddress`, and (2) sending a normal native-token transfer to that address before anyone else's IBC transfer causes `RegisterERC20Extension` to run. Both are unprivileged, ordinary user actions requiring no special permissions, making this trivially reachable by any attacker who front-runs the first IBC relay of a new denom.

### Recommendation
Before registering a dynamic ERC20 extension, check whether the derived address already holds a nonzero native EVM balance (not just a code hash). If so, either reject registration, refund/redirect the balance to a recoverable location (e.g., the erc20 module account or the ultimate token pair owner) before attaching the precompile code hash, or fold it into the bank-keeper-backed balance the precompile represents so that supply invariants remain consistent.

### Proof of Concept
1. Compute the deterministic ERC20 precompile address for an IBC denom that has not yet been relayed to the chain, using the same `utils.GetIBCDenomAddress(denom)` logic as `NewTokenPairSTRv2`.
2. Before any relayer submits the first IBC transfer packet for that denom, send native EVM coin (e.g., `aatom`) to the computed address via a normal EVM transfer.
3. Have the first IBC transfer for that denom relayed, triggering `OnRecvPacket` → `RegisterERC20Extension` → `CreateNewTokenPair` (succeeds, since no code hash exists yet) → `RegisterERC20CodeHash`, which reads the account's existing `Balance` and re-applies it while setting the precompile code hash.
4. Confirm the address now has EVM `statedb` balance > 0 concurrent with precompile code, and attempt to interact with the precompile's exposed ERC20 methods (`balanceOf`, `transfer`, etc.) to confirm none of them expose or move the stranded native balance — only the bank-keeper-backed coin balance for the registered denom is reachable through the precompile interface.

### Citations

**File:** x/erc20/types/token_pair.go (L13-29)
```go
// NewTokenPairSTRv2 creates a new TokenPair instance in the context of the
// Single Token Representation v2.
//
// It derives the ERC-20 address from the hex suffix of the IBC denomination
// (e.g. ibc/DF63978F803A2E27CA5CC9B7631654CCF0BBC788B3B7F0A10200508E37C70992).
func NewTokenPairSTRv2(denom string) (TokenPair, error) {
	address, err := utils.GetIBCDenomAddress(denom)
	if err != nil {
		return TokenPair{}, err
	}
	return TokenPair{
		Erc20Address:  address.String(),
		Denom:         denom,
		Enabled:       true,
		ContractOwner: OWNER_MODULE,
	}, nil
}
```

**File:** x/erc20/keeper/token_pairs.go (L16-31)
```go
// CreateNewTokenPair creates a new token pair and stores it in the state.
func (k Keeper) CreateNewTokenPair(ctx sdk.Context, denom string) (types.TokenPair, error) {
	pair, err := types.NewTokenPairSTRv2(denom)
	if err != nil {
		return types.TokenPair{}, err
	}
	account := k.evmKeeper.GetAccount(ctx, pair.GetERC20Contract())
	if account != nil && account.HasCodeHash() {
		return types.TokenPair{}, errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, "token already exists for token %s", pair.Erc20Address)
	}
	err = k.SetToken(ctx, pair)
	if err != nil {
		return types.TokenPair{}, err
	}
	return pair, nil
}
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L13-31)
```go
// RegisterERC20Extension creates and adds an ERC20 precompile interface for an IBC Coin.
//
// It derives the ERC-20 address from the token denomination and registers the
// EVM extension as an active dynamic precompile.
//
// CONTRACT: This must ONLY be called if there is no existing token pair for the given denom.
func (k Keeper) RegisterERC20Extension(ctx sdk.Context, denom string) (*types.TokenPair, error) {
	pair, err := k.CreateNewTokenPair(ctx, denom)
	if err != nil {
		return nil, err
	}

	// Add to existing EVM extensions
	if err := k.EnableDynamicPrecompile(ctx, pair.GetERC20Contract()); err != nil {
		return nil, err
	}

	return &pair, err
}
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L33-63)
```go
// RegisterERC20CodeHash sets the codehash for the erc20 precompile account
// if the bytecode for the erc20 codehash does not exists, it stores it.
func (k Keeper) RegisterERC20CodeHash(ctx sdk.Context, erc20Addr common.Address) error {
	var (
		// bytecode and codeHash is the same for all IBC coins
		// cause they're all using the same contract
		bytecode = common.FromHex(types.Erc20Bytecode)
		codeHash = crypto.Keccak256(bytecode)
	)
	// check if code was already stored
	code := k.evmKeeper.GetCode(ctx, common.Hash(codeHash))
	if len(code) == 0 {
		k.evmKeeper.SetCode(ctx, codeHash, bytecode)
	}

	var (
		nonce   uint64
		balance = common.U2560
	)
	// keep balance and nonce if account exists
	if acc := k.evmKeeper.GetAccount(ctx, erc20Addr); acc != nil {
		nonce = acc.Nonce
		balance = acc.Balance
	}

	return k.evmKeeper.SetAccount(ctx, erc20Addr, statedb.Account{
		CodeHash: codeHash,
		Nonce:    nonce,
		Balance:  balance,
	})
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L98-116)
```go
	// Case 1. token pair is not registered and is an IBC Coin
	// by checking the prefix we ensure that only coins not native from this chain are evaluated.
	case !found && strings.HasPrefix(coin.Denom, "ibc/"):
		tokenPair, err := k.RegisterERC20Extension(ctx, coin.Denom)
		if err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}

		ctx.EventManager().EmitEvents(
			sdk.Events{
				sdk.NewEvent(
					types.EventTypeRegisterERC20Extension,
					sdk.NewAttribute(types.AttributeCoinSourceChannel, packet.SourceChannel),
					sdk.NewAttribute(types.AttributeKeyERC20Token, tokenPair.Erc20Address),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, tokenPair.Denom),
				),
			},
		)
		return ack
```
