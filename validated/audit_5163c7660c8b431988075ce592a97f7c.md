I found a concrete analog. The bug class from the report — "a state transition requires an exact-zero condition (balance/lock == 0) that an unprivileged attacker can permanently block by front-running with dust" — maps to `Keeper.RegisterERC20Extension` / `CreateNewTokenPair` in `x/erc20`, which is triggered permissionlessly via the ICS20 `OnRecvPacket` middleware callback.

### Title
Permanent DoS of IBC coin ERC20 auto-registration via front-run deployment at the deterministic STRv2 contract address - (File: `x/erc20/keeper/token_pairs.go`, `x/erc20/keeper/dynamic_precompiles.go`, `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`RegisterERC20Extension` is called permissionlessly for *any* first-time IBC coin receipt via `OnRecvPacket` [1](#0-0) . It calls `CreateNewTokenPair`, which derives a deterministic ERC-20 contract address from the denom (`NewTokenPairSTRv2`) and refuses to proceed if an account with code already exists at that address [2](#0-1) . Because the address is fully deterministic from the (predictable) IBC denom, an attacker can pre-compute it for any channel/base-denom pair and deploy a contract there before the first IBC transfer of that denom arrives, permanently blocking automatic ERC20 registration for that IBC asset — analogous to the report's dust-lock front-running that permanently blocks a required "must reach empty/zero state" precondition.

### Finding Description
`CreateNewTokenPair` is the exact code path with the vulnerable precondition: [2](#0-1) 
It checks `account.HasCodeHash()` at the STRv2-derived address and errors out with `ErrTokenPairAlreadyExists` if code is present — mirroring the report's "must be in a clean/empty state" gate. Any address on the EVM state tree is attacker-writable prior to first use (deploy a contract via `CREATE`/`CREATE2` or via `evmKeeper.SetAccount`-equivalent EVM contract creation), so a bad actor who can predict the future IBC denom (voucher denom is deterministic from port/channel/base-denom, which are public parameters of any newly opened IBC channel) can pre-deploy trivial bytecode to that address. Any subsequent legitimate `OnRecvPacket` triggering `RegisterERC20Extension` → `CreateNewTokenPair` fails permanently with `ErrTokenPairAlreadyExists`, and this failure propagates back up as an IBC error acknowledgement [3](#0-2) .

Because `RegisterERC20Extension` explicitly documents "CONTRACT: This must ONLY be called if there is no existing token pair for the given denom" [4](#0-3) , there is no fallback/alternate-address mechanism; the address derivation is fixed by protocol (STRv2), so once blocked, the affected IBC denom can never get a native, dynamically-registered ERC20 precompile through this automatic path.

### Impact Explanation
This is a permanent, unprivileged, front-runnable griefing vector against a specific IBC-token-to-ERC20 registration, which the repository's own IBC middleware treats as a core value-bridging mechanism (`Case 1` of `OnRecvPacket`). While it does not directly mint/burn/duplicate tokens, it permanently freezes the ability to obtain a spendable ERC20/EVM-visible representation of an IBC asset for that denom — i.e., it locks value that would otherwise be spendable through EVM tooling/precompile-mediated balances, consistent with the "permanent freezing … of token-pair-backed balances" allowed-impact category. The underlying bank-side coins remain transferable, so the impact is scoped to the ERC20/precompile-visible representation rather than total loss of funds, which affects the severity relative to a pure fund-theft bug; I flag this uncertainty because I was not able to fully trace whether a governance-driven `MsgRegisterERC20`/`RegisterCoinProposal` provides an alternate deterministic-address scheme or recovery path for a denom already blocked this way (I did not find one in the reachable code, but the token-pair registry and STRv2 derivation logic in `x/erc20/types/token_pair.go` were not fully inspected in this session).

### Likelihood Explanation
Likelihood is high for any chain that has not yet received a first transfer of a given IBC denom: channel/port IDs and base denoms are public before the first transfer completes, so the resulting voucher/STRv2 address is computable off-chain in advance, and deploying a contract to an arbitrary EVM address is a completely permissionless, low-cost EVM transaction.

### Recommendation
Do not gate `CreateNewTokenPair` solely on "no code at the deterministic address." Instead: (1) reserve/allocate the STRv2 address via a dedicated nonce/counter or module-controlled `CREATE2`-style scheme not predictable purely from public denom/channel data before the first legitimate registration attempt, or (2) allow `RegisterERC20Extension`/governance to select an alternate deterministic salt/fallback address when the primary one is already occupied by non-module code, and emit a clear on-chain event so the denom is not silently and permanently unable to be represented as an ERC20.

### Proof of Concept
1. Observe (or set up) a new IBC channel between chain A and chain B for a token that has never been transferred to the EVM chain before receiving.
2. Off-chain, compute the STRv2 ERC-20 address that `NewTokenPairSTRv2(ibcDenom)` would derive for the eventual voucher denom (this only depends on the deterministic IBC denom trace and does not require any prior on-chain action from the eventual asset holder).
3. Before any real IBC transfer of that denom occurs, submit an ordinary EVM transaction that deploys arbitrary contract bytecode to that exact address (e.g. via a `CREATE2` factory tuned to hit the target address, or simply if the address happens to be reachable by conventional deployment nonces from a chosen deployer).
4. Have the legitimate IBC transfer executed; `OnRecvPacket` calls `RegisterERC20Extension` → `CreateNewTokenPair`, which now finds `account.HasCodeHash() == true` at the target address and returns `ErrTokenPairAlreadyExists`, causing `OnRecvPacket` to return an error acknowledgement [3](#0-2) .
5. Confirm that this failure is permanent and repeatable for every subsequent transfer of the same denom, since the check is purely deterministic on the fixed contract address.

**Caveat:** I was not able to fully verify (within available tool budget) the exact STRv2 address-derivation function (`types.NewTokenPairSTRv2`) to confirm the address is derivable purely from public, pre-transfer data with no unpredictable salt, nor whether `MsgRegisterERC20`/governance registration paths share this exact same address-collision weakness or have a distinct mitigation. A Devin session with full repository access should verify `x/erc20/types/token_pair.go`'s `NewTokenPairSTRv2` implementation and the governance-driven registration path before finalizing severity.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L98-104)
```go
	// Case 1. token pair is not registered and is an IBC Coin
	// by checking the prefix we ensure that only coins not native from this chain are evaluated.
	case !found && strings.HasPrefix(coin.Denom, "ibc/"):
		tokenPair, err := k.RegisterERC20Extension(ctx, coin.Denom)
		if err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/keeper/token_pairs.go (L16-30)
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
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L13-20)
```go
// RegisterERC20Extension creates and adds an ERC20 precompile interface for an IBC Coin.
//
// It derives the ERC-20 address from the token denomination and registers the
// EVM extension as an active dynamic precompile.
//
// CONTRACT: This must ONLY be called if there is no existing token pair for the given denom.
func (k Keeper) RegisterERC20Extension(ctx sdk.Context, denom string) (*types.TokenPair, error) {
	pair, err := k.CreateNewTokenPair(ctx, denom)
```
