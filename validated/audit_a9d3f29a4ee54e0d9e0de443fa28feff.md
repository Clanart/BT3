### Title
Reward withdrawal panic in staking `BeforeDelegationSharesModified` hook can permanently freeze a delegator's stake - ([File: sei-cosmos/x/distribution/keeper/hooks.go])

### Summary
The distribution module's `BeforeDelegationSharesModified` staking hook unconditionally panics if the synchronous reward payout it triggers fails, and this hook runs on every user-initiated delegation change (delegate, undelegate, begin-redelegate). Unlike the sibling `AfterValidatorRemoved` path, which was hardened with a `canReceiveWithdrawAddr` pre-check to avoid panicking on an unpayable withdraw address, `BeforeDelegationSharesModified` performs no such check before calling `withdrawDelegationRewards`, which ultimately calls `bankKeeper.SendCoinsFromModuleToAccount` to the delegator's configured withdraw address. [1](#0-0) 

### Finding Description
This mirrors the reported bug class: an unrelated "core" user operation (mint/withdraw of an nToken) is made to depend on an unconditional, synchronous external-value-transfer that can revert due to a blacklist/pause condition, thereby locking out all future core operations for that user.

In sei-chain's staking/distribution module:
- `SetWithdrawAddr` lets any delegator configure an arbitrary account as their reward withdraw address, validated only at set-time via `CanSendTo`/`BlockedAddr`: [2](#0-1) 

- Every `MsgDelegate`, `MsgUndelegate`, and `MsgBeginRedelegate` call triggers `BeforeDelegationSharesModified`, which calls `withdrawDelegationRewards` and panics on any error: [1](#0-0) 

- The developers already recognized and fixed this exact class of failure for `AfterValidatorRemoved` (an EndBlock-triggered path), explicitly checking `canReceiveWithdrawAddr` before sending and falling back to the community pool instead of panicking, precisely because "attempting the send and panicking on the resulting bank error would halt the chain": [3](#0-2) [4](#0-3) 

However, `BeforeDelegationSharesModified` was not given the same treatment. If the delegator's withdraw address later becomes unable to receive funds — for example, because a bank `BeforeSend`/blocklist hook tied to one of the reward denoms (an IBC or tokenfactory denom, or a CW20/ERC20 pointer-backed denom) starts rejecting transfers to that address after it was set (analogous to a blacklist/pause activating on the REWARD_TOKEN in the original report) — every subsequent delegate/undelegate/redelegate operation from that delegator will panic inside the hook before the delegation state is ever modified.

### Impact Explanation
Because the panic occurs in `BeforeDelegationSharesModified`, which runs before the delegation shares are changed, the delegator becomes permanently unable to delegate more, undelegate, or redelegate their existing stake through the normal staking message flow — their principal is effectively frozen as long as the withdraw address (or any reward denom's transfer path) remains blocked. This matches the "permanent freezing" impact category: unlike a single failed transaction, the condition is durable and self-reinforcing, since the reward accrues every block and the send will keep failing on every future attempt. This is directly analogous to the reported nToken issue, where an immediate, mandatory reward-token transfer during a core state-changing operation becomes a single point of failure for that operation going forward.

### Likelihood Explanation
Reaching this requires (1) a delegator having set (or having) a withdraw address that, at some later point, cannot receive one of the reward denoms — which is plausible if reward denoms other than `usei` are involved (e.g., IBC-transferred assets or tokenfactory-issued tokens with bank hooks that can later reject a specific recipient) — and (2) that delegator subsequently trying to delegate/undelegate/redelegate. Since `SetWithdrawAddr` is fully permissionless and only validated at set-time (not at withdrawal-time for every denom the account might later hold rewards in), and hook-driven denom transfer restrictions can change state over time, this is reachable by an ordinary transaction sender without any privileged access.

### Recommendation
Apply the same defensive pattern already used in `AfterValidatorRemoved` to `BeforeDelegationSharesModified`/`withdrawDelegationRewards`: verify the withdraw address (and each reward denom) can actually receive the payout before attempting the send, and if not, fail gracefully (return a normal error to the message handler, or hold rewards in an accessible/claimable pending state) instead of panicking. More generally, `withdrawDelegationRewards` should never let a `bankKeeper.SendCoins*` failure panic inside a hook that gates core delegation state transitions; the error should propagate as a normal, non-panicking message failure that leaves the delegation state so a fixed withdraw address does not brick the user's principal.

### Proof of Concept
Conceptual reproduction (not directly executable without a wasm/bank hook harness):
1. Delegator calls `MsgSetWithdrawAddr` to set their withdraw address to an account `W` that currently passes `CanSendTo`/`BlockedAddr` checks.
2. Rewards accrue in one or more denoms (including a denom whose transfers are gated by a bank hook, e.g. a tokenfactory "before send" hook or a CW20/ERC20 pointer bridge).
3. The denom's governing hook contract/admin later blocks `W` from receiving that denom (blacklist activated, analogous to `REWARD_TOKEN.transfer` reverting for a blacklisted address in the original report).
4. Delegator calls `MsgDelegate`/`MsgUndelegate`/`MsgBeginRedelegate`. Staking's `BeforeDelegationCreated`/`BeforeDelegationSharesModified` hook invokes `withdrawDelegationRewards`, whose `SendCoinsFromModuleToAccount` call to `W` fails.
5. `BeforeDelegationSharesModified` panics per [1](#0-0) , aborting the transaction; every future delegate/undelegate/redelegate attempt from this delegator repeats step 4-5, permanently freezing their staking operations.

Note: I was not able to fully trace every bank "BeforeSend"/denylist hook variant that could cause `SendCoinsFromModuleToAccount` to fail for a previously-valid withdraw address (e.g., the exact tokenfactory/wasm binding that revokes send permission after the fact), so the precise trigger mechanism for step 3 should be verified against the current tokenfactory/wasm-bridge hook implementations before treating this as fully confirmed.

### Citations

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L49-72)
```go
			// GetDelegatorWithdrawAddr falls back to the delegator (accAddr) when the
			// configured withdraw address cannot receive funds, but that fallback can
			// itself be unable to receive — e.g. accAddr is an EVM address whose Sei
			// mapping was re-associated to a different address, so CanAddressReceive
			// rejects it. This hook runs in EndBlock, so attempting the send and
			// panicking on the resulting bank error would halt the chain. Check
			// receivability first: when the recipient cannot receive, route the
			// commission to the community pool instead. The coins already back the
			// distribution module account (where community pool funds are held), so this
			// conserves value and avoids the partial module-account debit that a failed
			// SendCoins leaves behind.
			if h.k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
				if err := h.k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, coins); err != nil {
					panic(err)
				}
			} else {
				feePool := h.k.GetFeePool(ctx)
				decCoins, err := sdk.NewDecCoinsFromCoins(coins...)
				if err != nil {
					panic(err)
				}
				feePool.CommunityPool = feePool.CommunityPool.Add(decCoins...)
				h.k.SetFeePool(ctx, feePool)
			}
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L105-113)
```go
// withdraw delegation rewards (which also increments period)
func (h Hooks) BeforeDelegationSharesModified(ctx sdk.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress) {
	val := h.k.stakingKeeper.Validator(ctx, valAddr)
	del := h.k.stakingKeeper.Delegation(ctx, delAddr, valAddr)

	if _, err := h.k.withdrawDelegationRewards(ctx, val, del); err != nil {
		panic(err)
	}
}
```

**File:** sei-cosmos/x/distribution/keeper/keeper.go (L56-73)
```go
// SetWithdrawAddr sets a new address that will receive the rewards upon withdrawal
func (k Keeper) SetWithdrawAddr(ctx sdk.Context, delegatorAddr sdk.AccAddress, withdrawAddr sdk.AccAddress) error {
	// Reject any address the bank keeper would block from receiving funds, not just
	// the module-account blocklist: BlockedAddr also covers dynamically-derived
	// addresses such as the EVM coinbase addresses. Rejecting them here prevents a
	// delegator from parking an unpayable withdraw address that would later make the
	// force-withdraw in AfterValidatorRemoved panic during EndBlock.
	if k.blockedAddrs[withdrawAddr.String()] || k.bankKeeper.BlockedAddr(withdrawAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive external funds", withdrawAddr)
	}

	if !k.GetWithdrawAddrEnabled(ctx) {
		return types.ErrSetWithdrawAddrDisabled
	}

	if !k.bankKeeper.CanSendTo(ctx, withdrawAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidRecipient, "%s is not allowed to receive external funds", withdrawAddr)
	}
```

**File:** sei-cosmos/x/distribution/keeper/keeper.go (L86-96)
```go
func (k Keeper) canReceiveWithdrawAddr(ctx sdk.Context, withdrawAddr sdk.AccAddress) bool {
	// BlockedAddr mirrors the gate SendCoinsFromModuleToAccount actually enforces:
	// beyond the module-account blocklist it also rejects dynamically-derived
	// addresses such as the EVM coinbase addresses (an "evm_coinbase"-prefixed
	// address), which CanSendTo does not catch. Consulting it here keeps this
	// predicate in lockstep with the send, so AfterValidatorRemoved never concludes
	// an address is receivable and then panics on the resulting bank error.
	return !k.blockedAddrs[withdrawAddr.String()] &&
		!k.bankKeeper.BlockedAddr(withdrawAddr) &&
		k.bankKeeper.CanSendTo(ctx, withdrawAddr)
}
```
