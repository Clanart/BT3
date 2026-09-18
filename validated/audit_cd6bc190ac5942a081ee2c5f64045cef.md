### Title
`MsgCreateVestingAccount` recipient vesting can be permanently denied via account-preexistence front-run - ([File: sei-cosmos/x/auth/vesting/msg_server.go])

### Summary
`msgServer.CreateVestingAccount` refuses to create a vesting account for a recipient if any account already exists at that address, using a single, coarse `ak.GetAccount(ctx, to) != nil` check that does not distinguish "already a vesting account" from "any base account created by an unrelated action." A griefer can front-run a pending `MsgCreateVestingAccount` by sending a trivial amount of any coin to the intended recipient (or otherwise causing an account record to be created there), which auto-creates a `BaseAccount` for that address. The subsequent legitimate vesting-account creation then permanently reverts with "account already exists," denying the intended vest — the same bug class as the referenced Vader `LinearVesting.vestFor` finding.

### Finding Description
`CreateVestingAccount` is called by a `from` address to set up a vesting schedule for a `to` address on that recipient's behalf (a `vestFor`-style call, not requiring the recipient's own signature): [1](#0-0) 

The guard is:
```go
if acc := ak.GetAccount(ctx, to); acc != nil {
    return nil, sdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "account %s already exists", msg.ToAddress)
}
```
This rejects creation for *any* pre-existing account, not just an existing vesting account. In `sei-cosmos`, a `BaseAccount` is silently auto-created for a recipient the moment any `bank.SendCoins` targets it, if it doesn't already exist: [2](#0-1) 

Because normal bank sends are a permissionless, cheap, unprivileged transaction (a plain `MsgSend`/`MsgMultiSend` for the smallest denomination unit), any observer who sees a pending `MsgCreateVestingAccount` for recipient `to` in the mempool can front-run it with a trivial send to `to`. That send auto-creates a `BaseAccount` for `to` before the vesting-creation transaction executes. When the original transaction then runs, `ak.GetAccount(ctx, to) != nil` is true, and `CreateVestingAccount` returns `ErrInvalidRequest` — the vesting account is never created, and (unlike the intended flow) the coins the `from` account meant to lock into vesting are never sent, since the `SendCoins` call happens only *after* the account/vesting-object is set up. There is no alternate way to convert an already-materialized `BaseAccount` into a vesting account later; the code path only allows creation "from scratch."

This mirrors the referenced Vader bug exactly: a one-time "setup-for-another-address" call is guarded by an existence check keyed solely on the target address, and any unprivileged party can trivially and cheaply pre-populate that address's existence to permanently block the legitimate setup for that specific beneficiary.

### Impact Explanation
This denies the specific intended beneficiary's vesting grant permanently and unconditionally for as long as the griefer chooses to repeat the front-run (each attempt costs the griefer only the transfer of a minimal coin amount plus gas/fees, while the legitimate `from` account's `MsgCreateVestingAccount` transaction, though it fails cleanly with an error rather than losing funds directly in this implementation, is denied indefinitely — a targeted party can be selectively excluded from receiving a promised vesting grant). This is a fund-availability-denial impact against a chosen target, satisfying the "permanent freezing"/denial-of-service class described in the analog report.

### Likelihood Explanation
Likelihood is high in principle: the attack requires only observing a pending `MsgCreateVestingAccount` in the mempool (or knowing the recipient address in advance) and submitting a trivial `MsgSend` to the recipient before the vesting tx is included. No special privileges, precompile access, or validator collusion are needed — a single unprivileged transaction sender suffices.

However, note an important caveat found during investigation: `CreateVestingAccount` is gated by `s.creationDeprecated(ctx)`, which returns `true` (i.e., the whole message is rejected with `types.ErrVestingDeprecated`) on any chain not in the `chainsWithVestingHistory` allow-list (`pacific-1`, `atlantic-2`, `arctic-1`), and even on those chains, it becomes permanently disabled once the `"v6.7"` upgrade activates: [3](#0-2) 
So this code path is only reachable pre-deprecation-upgrade on the three named chains (for historical-replay compatibility) — meaning on a current/fresh deployment past that upgrade height, the entire endpoint is inert and this finding has no live exploitability. I could not verify from the indexed code whether `pacific-1` (mainnet) has already passed the `v6.7` upgrade height; if it has, this issue is already moot in practice.

### Recommendation
- Key the "already exists" check specifically off whether a *vesting* account already exists at `to` (e.g., checking the account's type is `*vestingtypes.BaseVestingAccount` or similar), rather than any account existence.
- If a plain `BaseAccount` already exists at `to` (e.g., due to a prior transfer), allow `CreateVestingAccount` to *upgrade* that account into a vesting account in place, similar to how `RegisterPointer` in `x/evm/keeper/msg_server.go` handles pre-existing pointer state via migration rather than hard failure.
- Alternatively/additionally, since the entire path is being deprecated, ensure the deprecation is fully rolled out across all chains before considering this exploitable in production; if any chain remains pre-deprecation-upgrade, treat this as an active issue.

### Proof of Concept
1. `from` broadcasts `MsgCreateVestingAccount{FromAddress: from, ToAddress: victim, Amount: ..., EndTime: ..., Delayed: true}` (only reachable if the current chain is in `chainsWithVestingHistory` and pre-`v6.7` upgrade height).
2. An attacker observing the mempool broadcasts (and gets included first) a `MsgSend{FromAddress: attacker, ToAddress: victim, Amount: 1usei}`.
3. `bank.Keeper.SendCoins` executes and, since `victim` has no account yet, auto-creates a `BaseAccount` for `victim` per `sei-cosmos/x/bank/keeper/send.go:164-182`.
4. The original `MsgCreateVestingAccount` transaction is then processed: `ak.GetAccount(ctx, to)` now returns the newly created `BaseAccount`, and the handler returns `sdkerrors.ErrInvalidRequest` at `sei-cosmos/x/auth/vesting/msg_server.go:96-98`, permanently denying the vesting grant to `victim` (repeatable by the attacker on every retry).

### Citations

**File:** sei-cosmos/x/auth/vesting/msg_server.go (L50-63)
```go
// creationDeprecated reports whether vesting account creation is disabled at
// the current block. Chains with pre-deprecation history keep the original
// behavior below the deprecation upgrade height so historical replay is
// unchanged; all other chains reject immediately.
func (s msgServer) creationDeprecated(ctx sdk.Context) bool {
	if _, ok := chainsWithVestingHistory[ctx.ChainID()]; !ok {
		return true
	}
	// The done-height lookup must not consume gas: the pre-deprecation handler
	// performed no store reads before its first bank check, so charging gas
	// here would alter gas usage of historical transactions during replay.
	gasFreeCtx := ctx.WithGasMeter(sdk.NewInfiniteGasMeter(1, 1))
	return s.upgradeKeeper.IsUpgradeActiveAtHeight(gasFreeCtx, DeprecationUpgradeName, ctx.BlockHeight())
}
```

**File:** sei-cosmos/x/auth/vesting/msg_server.go (L83-98)
```go
	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	if bk.BlockedAddr(to) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}

	if acc := ak.GetAccount(ctx, to); acc != nil {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrInvalidRequest, "account %s already exists", msg.ToAddress)
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L164-182)
```go
func (k BaseSendKeeper) SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	if err := k.SendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt); err != nil {
		return err
	}

	// Create account if recipient does not exist.
	//
	// NOTE: This should ultimately be removed in favor a more flexible approach
	// such as delegated fee messages.
	accExists := k.ak.HasAccount(ctx, toAddr)
	if !accExists {
		defer func() {
			recordNewAccounts(ctx.Context(), 1)
		}()
		k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, toAddr))
	}

	return nil
}
```
