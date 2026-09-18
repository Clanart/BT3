### Title
Address-association balance migration silently strands staking/vesting-locked funds under the pre-association cast identity - ([File: utils/helpers/associate.go])

### Summary
The reported bug class is: two representations of the same account/asset are initialized/populated independently (L1 ECO vs. L2 ECO inflation multiplier), and the "sync" step that is supposed to unify them only handles part of the state, leaving a residual divergence that causes fund loss/inaccessibility. Sei-chain has a structurally analogous pattern in its EVM↔Sei **dual address model**: before explicit association, an EVM address has a deterministic "cast" Sei identity (`sdk.AccAddress(evmAddr[:])`) that can independently accumulate on-chain value (bank balance, wei, and **locked/staked/vesting** amounts). When the account later associates its real pubkey-derived Sei address, `AssociationHelper.MigrateBalance` only migrates `SpendableCoins` and the wei balance — it explicitly leaves `LockedCoins` behind at the cast address, which has no reachable spending path afterward.

### Finding Description
Every EVM address has two possible Sei-side identities documented in the module itself: an explicit, pubkey-derived association, and a fallback "cast" address obtained by reinterpreting the EVM address bytes as an `AccAddress`. [1](#0-0) 

Before a user runs `associate`/`associatePubKey` (or before the ante preprocessor auto-associates on their first signed tx), any Cosmos-side operation that resolves the cast address (bank sends, delegations via `MsgDelegate` addressed by cast bech32 string, tokenfactory/community mechanisms, or anything using `GetSeiAddressOrDefault`) can leave value at `castAddr := sdk.AccAddress(evmAddr[:])`, including amounts that become `LockedCoins` (delegated/vesting balances) as opposed to merely `SpendableCoins`.

When association finally happens, `AssociateAddresses` calls `MigrateBalance`, which:
- migrates `SpendableCoins` (or only `usei` when `migrateUseiOnly`) from `castAddr` to `seiAddr`,
- migrates the wei balance,
- and only removes the `castAddr` base account if `LockedCoins` at `castAddr` is zero. [2](#0-1) 

Critically, there is **no migration path for `LockedCoins`** (e.g., staking delegations or vesting balances recorded against the cast address). After `SetAddressMapping(ctx, seiAddr, evmAddr)` runs, all future EVM↔Sei resolution (`GetSeiAddress`, `getSeiAddr` precompile, `GetSeiAddressOrDefault`) routes exclusively to the pubkey-derived `seiAddr` [3](#0-2) 
so the cast address ceases to be reachable through any of the standard EVM-facing paths. The staking precompile's own comments corroborate that this is a recognized, real class of orphaning: delegations created under the cast identity cannot be "safely merged" and become orphaned once association happens. [4](#0-3) [5](#0-4) 

This mirrors the audited bug precisely: initialization/onboarding populates one identity's state (cast address) independently of the other (associated address); the "sync" operation (`MigrateBalance`, analogous to the missing `rebase()` call in the ECO report) exists but is incomplete — it only reconciles a subset of the value classes (spendable + wei) and knowingly skips `LockedCoins`, permanently leaving that subset of value stuck at an address that is no longer the canonical resolution target for the EVM address.

### Impact Explanation
Any usei that reached the cast address as `LockedCoins` (staking delegations, unbonding, or vesting-locked balances) prior to association becomes permanently unreachable through the EVM address's canonical identity once association occurs — the account can no longer be resolved to via `getSeiAddr`/`GetSeiAddress`, and standard EVM-side flows (precompiles, `sendNative`, delegation via caller-derived address) all resolve to the new, disjoint `seiAddr`. This is a permanent freezing of user funds, matching the "Accept only concrete fund loss or permanent freezing" bar in the validation rules. It is architecturally identical to the ECO issue's core defect: an incomplete/partial synchronization routine at an identity-linking boundary that leaves value permanently split across two representations of "the same account."

### Likelihood Explanation
This requires no privileged access — any user can trigger it by interacting with the chain (e.g., delegating, or having a vesting account created) at their pre-association cast address before ever sending an `Associate`/`associatePubKey` transaction or otherwise triggering the ante-level auto-association in `EVMPreprocessDecorator.AnteHandle`. Reaching `LockedCoins > 0` at a cast address is plausible in ordinary usage (e.g., vesting grants configured against the byte-cast address, or a user delegating stake via the Cosmos side under the cast bech32 before ever using an EVM wallet flow). The code explicitly anticipates and works around a related case for delegation (blocking `delegate()` for un-associated callers, and specially handling SetCode/EIP-7702 authorities) but the general `MigrateBalance` path for ordinary association still does not migrate `LockedCoins`.

### Recommendation
Extend `AssociationHelper.MigrateBalance` (and its legacy variants) to also handle non-zero `LockedCoins` at the cast address before/while associating — either by:
- Rejecting/deferring association when `LockedCoins(castAddr)` is non-zero and instead exposing an explicit migration message that unlocks/transfers vesting or unbonds staking positions to the real `seiAddr`, or
- Providing a first-class migration routine that reassigns delegation/vesting records from `castAddr` to `seiAddr` atomically as part of association, mirroring how `rebase()` was recommended to be invoked as part of `L1ECOBridge.initialize` to keep both representations of state in sync.

### Proof of Concept
Not executable from static analysis alone; conceptually:
1. Before ever associating, get `LockedCoins(ctx, castAddr)` to be non-zero at `castAddr = sdk.AccAddress(evmAddr[:])` — e.g., issue a `MsgDelegate` (or set up a vesting account) using the cast bech32 address directly via the Cosmos side.
2. Trigger association for that EVM address (either an explicit `Associate`/`associatePubKey` call, or simply signing any EVM tx which triggers `EVMPreprocessDecorator.AnteHandle` → `AssociateAddresses`).
3. Observe that `MigrateBalance` moves `SpendableCoins` and wei but leaves the delegation/vesting-locked funds at `castAddr` [6](#0-5) 
, and that `castAddr` is no longer returned by `getSeiAddr`/`GetSeiAddress` for the EVM address, so no standard EVM-facing flow can reach it to withdraw/unbond those funds.

Note: I was not able to fully confirm whether Cosmos-native tooling still allows a user to independently sign transactions as the raw cast `AccAddress` to unwind delegations/vesting after association (this would depend on whether the underlying private key material used to sign EVM txs can also produce a valid Cosmos signature for the cast address, which is uncertain and outside what static code search can settle) — this uncertainty affects whether the freeze is truly permanent or merely requires an unusual recovery path. A Devin session with runtime/CLI access would be needed to conclusively determine reachability.

### Citations

**File:** x/evm/AGENTS.md (L9-16)
```markdown
## Dual Address Model

Every account can have both a Sei (bech32) address and an EVM (hex) address. The module maintains a bidirectional mapping between them.

- **Explicit association** — users can link their addresses via an Associate transaction or by signing any Cosmos/EVM transaction (the EVM address is derived from their secp256k1 public key).
- **Default (cast) addresses** — when no explicit association exists, the module falls back to a deterministic byte-cast. Cast addresses have limitations on receiving funds compared to fully associated addresses.

- **Why** - cast addresses will result in two views of the same account (e.g. two balances, etc.)
```

**File:** utils/helpers/associate.go (L34-83)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}

func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** precompiles/addr/legacy/v575/addr.go (L114-134)
```go
func (p PrecompileExecutor) getEvmAddr(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, err
	}

	seiAddr, err := sdk.AccAddressFromBech32(args[0].(string))
	if err != nil {
		return nil, err
	}

	evmAddr, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if !found {
		metrics.IncrementAssociationError("getEvmAddr", types.NewAssociationMissingErr(args[0].(string)))
		return nil, fmt.Errorf("sei address %s is not associated", args[0].(string))
	}
	return method.Outputs.Pack(evmAddr)
}
```

**File:** precompiles/staking/legacy/v67/staking.go (L407-412)
```go
}

// delegateFor performs the shared direct and authorized delegation steps. The
// delegator must be explicitly associated because a later association cannot
// safely merge a delegation created under its cast address.
func (p PrecompileExecutor) delegateFor(ctx sdk.Context, method *abi.Method, delegator sdk.AccAddress, delegatorEVM common.Address, validatorBech32 string, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM, execute stakingMessageExecutor) ([]byte, uint64, error) {
```

**File:** x/evm/ante/preprocess.go (L103-110)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)
```
