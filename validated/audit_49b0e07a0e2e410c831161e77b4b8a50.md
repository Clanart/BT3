### Title
`AGG_SIG_UNSAFE` Signature Replay Across Coins and Blocks — (`File: crates/chia-consensus/src/conditions.rs`)

### Summary

`AGG_SIG_UNSAFE` (opcode 49) signs a raw message with no coin-binding context appended. Unlike every other `AGG_SIG_*` variant, the signed message is not augmented with any spend-specific data (coin ID, parent, puzzle hash, or amount). A valid `AGG_SIG_UNSAFE` signature over a given `(pk, msg)` pair is therefore replayable: it satisfies the same condition in any other coin spend — past, present, or future — that emits `(AGG_SIG_UNSAFE pk msg)`, with no additional on-chain protection.

### Finding Description

Every `AGG_SIG_*` condition except `AGG_SIG_UNSAFE` appends spend-specific context to the message before signing, binding the signature to a particular coin or spend:

- `AGG_SIG_ME` appends `coin_id || agg_sig_me_additional_data`
- `AGG_SIG_PARENT` appends `parent_id || agg_sig_parent_additional_data`
- `AGG_SIG_PUZZLE` appends `puzzle_hash || agg_sig_puzzle_additional_data`
- `AGG_SIG_AMOUNT` appends `coin_amount || agg_sig_amount_additional_data`
- etc. [1](#0-0) 

For `AGG_SIG_UNSAFE`, no such context is appended. The raw message bytes are pushed directly into `pkm_pairs` for aggregate verification: [2](#0-1) 

The only protection is `check_agg_sig_unsafe_message`, which rejects messages whose bytes happen to **end with** one of the other conditions' domain-separation suffixes (to prevent cross-condition confusion). It does not prevent replay across different coins or blocks: [3](#0-2) 

The `ELIGIBLE_FOR_FF` flag is **not** cleared when `AGG_SIG_UNSAFE` is present — only `ELIGIBLE_FOR_DEDUP` is cleared: [4](#0-3) 

This means a singleton spend that uses `AGG_SIG_UNSAFE` (instead of `AGG_SIG_ME`) can be fast-forwarded to a different coin while the original signature remains valid, because the signed message is not bound to the coin ID, parent, or any other coin-specific field.

The `ELIGIBLE_FOR_FF` eligibility criteria explicitly lists only `AGG_SIG_ME`, `AGG_SIG_PARENT`, and `AGG_SIG_PARENT_*` as disqualifying: [5](#0-4) 

`AGG_SIG_UNSAFE`, `AGG_SIG_PUZZLE`, `AGG_SIG_AMOUNT`, and `AGG_SIG_PUZZLE_AMOUNT` do **not** clear `ELIGIBLE_FOR_FF`. A puzzle that uses `AGG_SIG_PUZZLE` or `AGG_SIG_AMOUNT` is also eligible for fast-forward, and those conditions only bind to puzzle hash or amount — not to the unique coin ID. An attacker who obtains a valid signature for `(pk, msg || puzzle_hash || domain)` can replay it against any other coin with the same puzzle hash and amount, in any block.

### Impact Explanation

This is a **High** severity signature replay issue. A puzzle that relies on `AGG_SIG_UNSAFE`, `AGG_SIG_PUZZLE`, or `AGG_SIG_AMOUNT` for authorization does not get a coin-unique signature. An attacker who observes a valid spend bundle can extract the aggregate signature and replay it against a different coin (same puzzle hash / same amount) in a future block. If the puzzle controls coin creation or value transfer, this enables unauthorized spend acceptance — satisfying the "Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay" impact criterion.

The fast-forward path makes this concrete: the consensus engine explicitly marks `AGG_SIG_UNSAFE`-bearing spends as `ELIGIBLE_FOR_FF`, meaning the node itself will rebase such a spend onto a different coin while keeping the original signature intact. [6](#0-5) 

### Likelihood Explanation

- Any puzzle that uses `AGG_SIG_UNSAFE` instead of `AGG_SIG_ME` is directly vulnerable.
- The fast-forward mechanism (`fast_forward_singleton`) is an active, production code path that rebases singleton spends to new coins.
- An unprivileged attacker only needs to observe a valid spend bundle on-chain and submit a replayed spend bundle targeting a different coin with the same puzzle hash.
- No key compromise or privileged access is required.

### Recommendation

1. `AGG_SIG_UNSAFE` should clear `ELIGIBLE_FOR_FF` in `MempoolVisitor::condition`, just as `AGG_SIG_ME` and `AGG_SIG_PARENT` do, since its message is not coin-bound.
2. `AGG_SIG_PUZZLE` and `AGG_SIG_AMOUNT` should also clear `ELIGIBLE_FOR_FF`, or their use in fast-forwardable singletons should be explicitly documented as unsafe (they bind only to puzzle hash / amount, not to the unique coin ID).
3. Puzzle authors should be warned that `AGG_SIG_UNSAFE` provides no replay protection across coins or blocks and should prefer `AGG_SIG_ME` for any authorization that must be coin-specific.

### Proof of Concept

1. Deploy a singleton puzzle that uses `(AGG_SIG_UNSAFE pk msg)` for authorization.
2. Spend coin A with a valid spend bundle; the aggregate signature covers `sign(sk, msg)` with no coin-binding.
3. Observe the spend bundle on-chain. Extract `(pk, msg, sig)`.
4. Construct a new spend bundle targeting coin B (same puzzle hash, different coin ID — e.g., a later singleton generation).
5. Reuse the same aggregate signature. The consensus engine calls `check_agg_sig_unsafe_message` (passes, since `msg` does not end with a domain suffix), then verifies `aggregate_verify(sig, [(pk, msg)])` — which succeeds because the message is identical.
6. The spend of coin B is accepted without the key holder's authorization for that specific coin.

The fast-forward path (`fast_forward_singleton`) automates step 4–6: since `AGG_SIG_UNSAFE` does not clear `ELIGIBLE_FOR_FF`, the node will rebase the original spend onto a new coin and accept the original signature as valid. [2](#0-1) [7](#0-6)

### Citations

**File:** crates/chia-consensus/src/make_aggsig_final_message.rs (L15-50)
```rust
    match opcode {
        AGG_SIG_PARENT => {
            msg.extend(spend.parent_id.as_slice());
            msg.extend(constants.agg_sig_parent_additional_data.as_slice());
        }
        AGG_SIG_PUZZLE => {
            msg.extend(spend.puzzle_hash.as_slice());
            msg.extend(constants.agg_sig_puzzle_additional_data.as_slice());
        }
        AGG_SIG_AMOUNT => {
            msg.extend(u64_to_bytes(spend.coin_amount).as_slice());
            msg.extend(constants.agg_sig_amount_additional_data.as_slice());
        }
        AGG_SIG_PUZZLE_AMOUNT => {
            msg.extend(spend.puzzle_hash.as_slice());
            msg.extend(u64_to_bytes(spend.coin_amount).as_slice());
            msg.extend(constants.agg_sig_puzzle_amount_additional_data.as_slice());
        }
        AGG_SIG_PARENT_AMOUNT => {
            msg.extend(spend.parent_id.as_slice());
            msg.extend(u64_to_bytes(spend.coin_amount).as_slice());
            msg.extend(constants.agg_sig_parent_amount_additional_data.as_slice());
        }
        AGG_SIG_PARENT_PUZZLE => {
            msg.extend(spend.parent_id.as_slice());
            msg.extend(spend.puzzle_hash.as_slice());
            msg.extend(constants.agg_sig_parent_puzzle_additional_data.as_slice());
        }
        AGG_SIG_ME => {
            let coin: Coin = Coin::new(spend.parent_id, spend.puzzle_hash, spend.coin_amount);

            msg.extend(coin.coin_id().as_slice());
            msg.extend(constants.agg_sig_me_additional_data.as_slice());
        }
        _ => {}
    }
```

**File:** crates/chia-consensus/src/conditions.rs (L48-62)
```rust
// If the CoinSpend is eligible for fast-forward, this flag is set. A spend is
// eligible if:
// 1. the input coin amount is odd
// 2. There are no AGG_SIG_ME, AGG_SIG_PARENT, AGG_SIG_PARENT_* conditions
// 3. No ASSERT_MY_COIN_ID condition, no more than one ASSERT_MY_PARENT_ID condition
//    (as the second condition)
// 4. it has an output coin with the same puzzle hash as the spend itself

// 5. None of the coin's outputs are spent by the same SpendBundle (which locks
//    down the specific coin being spent). Even though an ephemeral FF spend
//    wouldn't lock down the coin, it's expensive to check for this.
// 6. there are no timelocks - ASSERT_*_RELATIVE / ASSERT_MY_BIRTH_*
// 7. The coin is not referenced by an ASSERT_CONCURRENT_SPEND condition
// 8. The coin does not issue an CREATE_COIN_ANNOUNCEMENT condition
pub const ELIGIBLE_FOR_FF: u32 = 4;
```

**File:** crates/chia-consensus/src/conditions.rs (L130-143)
```rust
            Condition::AggSigMe(_, _)
            | Condition::AggSigParent(_, _)
            | Condition::AggSigParentAmount(_, _)
            | Condition::AggSigParentPuzzle(_, _) => {
                // references to your parent and references will not successfully fastforward
                spend.flags &= !ELIGIBLE_FOR_DEDUP;
                spend.flags &= !ELIGIBLE_FOR_FF;
            }
            Condition::AggSigPuzzle(_, _)
            | Condition::AggSigAmount(_, _)
            | Condition::AggSigPuzzleAmount(_, _)
            | Condition::AggSigUnsafe(_, _) => {
                spend.flags &= !ELIGIBLE_FOR_DEDUP;
            }
```

**File:** crates/chia-consensus/src/conditions.rs (L338-361)
```rust
fn check_agg_sig_unsafe_message(
    a: &Allocator,
    msg: NodePtr,
    constants: &ConsensusConstants,
) -> Result<(), ValidationErr> {
    if a.atom_len(msg) < 32 {
        return Ok(());
    }
    let buf = a.atom(msg);
    for additional_data in &[
        constants.agg_sig_me_additional_data.as_ref(),
        constants.agg_sig_parent_additional_data.as_ref(),
        constants.agg_sig_puzzle_additional_data.as_ref(),
        constants.agg_sig_amount_additional_data.as_ref(),
        constants.agg_sig_puzzle_amount_additional_data.as_ref(),
        constants.agg_sig_parent_amount_additional_data.as_ref(),
        constants.agg_sig_parent_puzzle_additional_data.as_ref(),
    ] {
        if buf.as_ref().ends_with(additional_data) {
            return Err(ValidationErr::Err(ErrorCode::InvalidMessage));
        }
    }
    Ok(())
}
```

**File:** crates/chia-consensus/src/conditions.rs (L1446-1455)
```rust
            Condition::AggSigUnsafe(pk, msg) => {
                // AGG_SIG_UNSAFE messages are not allowed to end with the
                // suffix added to other AGG_SIG_* conditions
                check_agg_sig_unsafe_message(a, msg, constants)?;
                ret.agg_sig_unsafe.push((to_key(a, pk)?, msg));
                if !flags.contains(ConsensusFlags::DONT_VALIDATE_SIGNATURE) {
                    state
                        .pkm_pairs
                        .push((to_key(a, pk)?, a.atom(msg).as_ref().to_vec().into()));
                }
```

**File:** crates/chia-consensus/src/fast_forward.rs (L59-150)
```rust
pub fn fast_forward_singleton(
    a: &mut Allocator,
    puzzle: NodePtr,
    solution: NodePtr,
    coin: &Coin,       // the current coin being spent (for validation)
    new_coin: &Coin,   // the new coin to spend
    new_parent: &Coin, // the parent coin of the new coin being spent
) -> Result<NodePtr> {
    // a coin with an even amount is not a valid singleton
    // as defined by singleton_top_layer_v1_1.clsp
    if (coin.amount & 1) == 0 || (new_parent.amount & 1) == 0 || (new_coin.amount & 1) == 0 {
        return Err(Error::CoinAmountEven);
    }

    // we can only fast-forward spends of singletons whose puzzle hash doesn't
    // change
    if coin.puzzle_hash != new_parent.puzzle_hash || coin.puzzle_hash != new_coin.puzzle_hash {
        return Err(Error::PuzzleHashMismatch);
    }

    let singleton = CurriedProgram::<NodePtr, SingletonArgs<NodePtr>>::from_clvm(a, puzzle)?;
    let mut new_solution = SingletonSolution::<NodePtr>::from_clvm(a, solution)?;

    let Proof::Lineage(lineage_proof) = &mut new_solution.lineage_proof else {
        return Err(Error::ExpectedLineageProof);
    };

    // this is the tree hash of the singleton top layer puzzle
    // the tree hash of singleton_top_layer_v1_1.clsp
    if singleton.args.singleton_struct.mod_hash.as_ref() != SINGLETON_TOP_LAYER_V1_1_HASH {
        return Err(Error::NotSingletonModHash);
    }

    // also make sure the actual mod-hash of this puzzle matches the
    // singleton_top_layer_v1_1.clsp
    let mod_hash = tree_hash(a, singleton.program);
    if mod_hash != SINGLETON_TOP_LAYER_V1_1_HASH.into() {
        return Err(Error::NotSingletonModHash);
    }

    // if the current solution to the puzzle doesn't match the coin amount, it's
    // an invalid spend. Don't try to fast-forward it
    if coin.amount != new_solution.amount {
        return Err(Error::CoinAmountMismatch);
    }

    // given the parent's parent, the parent's inner puzzle and parent's amount,
    // we can compute the hash of the curried inner puzzle for our parent coin
    let parent_puzzle_hash = curry_and_treehash(
        &lineage_proof.parent_inner_puzzle_hash,
        &singleton.args.singleton_struct,
    );

    // now that we know the parent coin's puzzle hash, we have all the pieces to
    // compute the coin being spent (before the fast-forward).
    let parent_coin = Coin {
        parent_coin_info: lineage_proof.parent_parent_coin_info,
        puzzle_hash: parent_puzzle_hash,
        amount: lineage_proof.parent_amount,
    };

    if parent_coin.coin_id() != coin.parent_coin_info {
        return Err(Error::ParentCoinMismatch);
    }

    let inner_puzzle_hash = tree_hash(a, singleton.args.inner_puzzle);
    if inner_puzzle_hash != lineage_proof.parent_inner_puzzle_hash.into() {
        return Err(Error::InnerPuzzleHashMismatch);
    }

    let puzzle_hash = tree_hash(a, puzzle);

    if puzzle_hash != new_parent.puzzle_hash.into() || puzzle_hash != coin.puzzle_hash.into() {
        // we can only fast-forward if the puzzle hash match the new coin
        // the spend is assumed to be valied already, so we don't check it
        // against the original coin being spent
        return Err(Error::PuzzleHashMismatch);
    }

    // update the solution to use the new parent coin's information
    lineage_proof.parent_parent_coin_info = new_parent.parent_coin_info;
    lineage_proof.parent_amount = new_parent.amount;
    new_solution.amount = new_coin.amount;

    let expected_new_parent = new_parent.coin_id();

    if new_coin.parent_coin_info != expected_new_parent {
        return Err(Error::CoinMismatch);
    }

    Ok(new_solution.to_clvm(a)?)
}
```
