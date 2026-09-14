### Title
Unbounded Restriction/Member Stacking in Custody Architecture Can Permanently Brick a Coin's Spendability - (File: chia/wallet/puzzles/custody/custody_architecture.py)

### Summary
The generic custody architecture (`PuzzleWithRestrictions`, `MofN`, `ValidatorStackRestriction`) lets an owner curry an arbitrary number of `restrictions` and/or `MofN` members into a single coin's puzzle. Every future spend of that coin must execute *all* of these restriction/member puzzles inside the CLVM program that runs the spend. There is no cap enforced anywhere in this code on how many restrictions or members can be stacked, so an owner (or a malicious co-signer in a shared custody setup) can grow this list until the cost of running a single spend of the coin exceeds `MAX_BLOCK_COST_CLVM`, making the coin permanently unspendable. This mirrors the zksync-sso report's root cause: an unbounded per-account collection (`hooks` there, `restrictions`/`members` here) that must be iterated/executed in full on every future transaction, eventually exceeding the execution budget.

### Finding Description
`PuzzleWithRestrictions.puzzle_reveal()` curries every restriction's puzzle (member-validator and dpuz-validator lists) into `RESTRICTION_MOD`, and `puzzle_hash()` mirrors this in the puzzle-hash computation: [1](#0-0) 

Likewise, `MofN` recursion (`MofNMerkleTree`, used for arbitrary M-of-N groupings of members) has no limit on the number of `members`, and `ValidatorStackRestriction.required_wrappers` similarly accepts an arbitrary list of wrapper puzzles that get chained into the delegated puzzle: [2](#0-1) 

None of these constructors, `curry` calls, or the wallet-side puzzle assembly enforce any maximum count on `restrictions`, `members`, or `required_wrappers`; there is no reference to `MAX_BLOCK_COST_CLVM` or any per-puzzle length limit anywhere in `chia/wallet/puzzles/custody/`. Every restriction and every M-of-N branch that gets curried in must be run by CLVM on every subsequent spend of the coin (each restriction puzzle validates the delegated-puzzle output, each `MofN` branch participates in the merkle proof/execution), so the aggregate CLVM cost of spending the coin scales with the number of restrictions/members that were configured when the puzzle was created. Because the puzzle hash is derived from the full restriction/member set, this is baked into the coin's puzzle hash at creation time and cannot be "pruned" later without a cooperative re-spend that itself has to pay the (potentially prohibitive) cost of validating the old puzzle.

This is directly analogous to the reported zksync-sso issue: `runExecutionHooks`/`runValidationHooks` iterate an `EnumerableSet` of hooks with no cap, so an account that accumulates too many hooks can no longer process any transaction. Here, an account/coin that accumulates too many restrictions or M-of-N members can no longer be spent within a single block's CLVM cost budget.

### Impact Explanation
If a coin's puzzle is configured (either by the owner themselves, by a wallet-provided default that doesn't cap the list, or by a malicious co-owner in a shared/multi-party custody arrangement) with enough restrictions/members that the *minimum possible* CLVM cost to satisfy them exceeds `constants.MAX_BLOCK_COST_CLVM`, the coin becomes permanently unspendable — funds are locked forever, since no spend bundle for that coin can ever be included in a block (`Err.BLOCK_COST_EXCEEDS_MAX` will always be raised, as enforced in `chia/consensus/block_body_validation.py`). This is a spend-triggered transaction-processing halt for that coin/account, equivalent in severity to the zksync-sso account bricking. In a multi-party custody scenario (M-of-N with restrictions), one signer contributing an oversized `MofN`/restriction structure at coin-creation time can render the entire group's funds unrecoverable even though a majority of signers agree to spend.

### Likelihood Explanation
Likelihood is moderate-to-low because it requires the puzzle to be deliberately (or carelessly) constructed with excessive restrictions/members — this is a design-time choice made by the wallet software or by whichever party assembles the `PuzzleWithRestrictions`/`MofN` structure, not something an unrelated third party can inject into someone else's existing coin. However, because there is no library-level bound and no warning, standard use of the flexible custody framework (e.g., programmatically generated M-of-N trees, or accepting untrusted restriction lists from a counterparty in a joint-custody negotiation) can trigger it without the user realizing the resulting puzzle is unspendable until it's too late.

### Recommendation
Add an explicit, enforced upper bound on the number of `restrictions` in `PuzzleWithRestrictions`, on `members` in `MofN`, and on `required_wrappers` in `ValidatorStackRestriction`, ideally computed from a conservative estimate of per-restriction/per-member CLVM cost so the worst-case total spend cost is guaranteed to stay well under `MAX_BLOCK_COST_CLVM` (leaving headroom for AGG_SIG/condition costs from the rest of the spend bundle). Reject construction (raise `ValueError`) when the estimated cost would exceed this bound, and document the limit for wallet integrators assembling multi-party custody structures.

### Proof of Concept
1. Construct a `PuzzleWithRestrictions` with `puzzle=ACSMember()` (or any member) and `restrictions=[ValidatorStackRestriction(required_wrappers=[...])]` where `required_wrappers` contains a very large number (e.g., thousands) of distinct wrapper puzzles, as demonstrated at small scale in [3](#0-2)  and [4](#0-3) .
2. Farm a coin to `pwr.puzzle_hash()`.
3. Attempt to spend the coin by satisfying every restriction/wrapper solution as in `test_dpuz_validator_stack_restriction`; measure the CLVM execution cost via `cost_logger`/`run_block_generator2`.
4. As the number of restrictions/wrappers grows, the cost of the minimal valid spend grows correspondingly; scale up until the cost of *any* satisfying spend exceeds `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`, confirmed by `mempool_manager.pre_validate_spendbundle` raising `ValidationError` with `Err.BLOCK_COST_EXCEEDS_MAX` (pattern shown at [5](#0-4) ), at which point the coin can never be included in any block and the funds are permanently locked.

### Citations

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L354-376)
```python
    def puzzle_reveal(self, _top_level: bool = True) -> Program:
        inner_puzzle = self.puzzle.puzzle(self.nonce)  # pylint: disable=assignment-from-no-return

        if len(self.restrictions) > 0:  # We optimize away the restriction layer when no restrictions are present
            restricted_inner_puzzle = RESTRICTION_MOD.curry(
                [restriction.puzzle(self.nonce) for restriction in self.restrictions if restriction.member_not_dpuz],
                [
                    restriction.puzzle(self.nonce)
                    for restriction in self.restrictions
                    if not restriction.member_not_dpuz
                ],
                inner_puzzle,
            )
        else:
            restricted_inner_puzzle = inner_puzzle

        if _top_level:
            fed_inner_puzzle = DELEGATED_PUZZLE_FEEDER.curry(restricted_inner_puzzle)
        else:
            fed_inner_puzzle = restricted_inner_puzzle

        return INDEX_WRAPPER.curry(self.nonce, fed_inner_puzzle)

```

**File:** chia/wallet/puzzles/custody/restriction_utilities.py (L19-62)
```python
@dataclass(kw_only=True, frozen=True)
class ValidatorStackRestriction:
    required_wrappers: list[MIPSComponent]

    @property
    def member_not_dpuz(self) -> bool:
        return False

    def memo(self, nonce: int) -> Program:
        return Program.to([wrapper.memo(nonce) for wrapper in self.required_wrappers])

    def required_quoted_wrappers_hashes(self, nonce: int) -> list[bytes32]:
        required_quoted_wrappers_hashes = []
        for wrapper in self.required_wrappers:
            puzhash = wrapper.puzzle_hash(nonce)
            required_quoted_wrappers_hashes.append(Program.to((1, puzhash)).get_tree_hash_precalc(puzhash))

        return required_quoted_wrappers_hashes

    def puzzle(self, nonce: int) -> Program:
        return ENFORCE_DPUZ_WRAPPERS.curry(QUOTED_ADD_DPUZ_WRAPPER_HASH, self.required_quoted_wrappers_hashes(nonce))

    def puzzle_hash(self, nonce: int) -> bytes32:
        return (
            Program.to(ENFORCE_DPUZ_WRAPPERS_HASH)
            .curry(QUOTED_ADD_DPUZ_WRAPPER_HASH, self.required_quoted_wrappers_hashes(nonce))
            .get_tree_hash_precalc(ENFORCE_DPUZ_WRAPPERS_HASH)
        )

    def solve(self, original_dpuz: Program) -> Program:
        return Program.to([original_dpuz.get_tree_hash()])

    def modify_delegated_puzzle_and_solution(
        self, delegated_puzzle_and_solution: DelegatedPuzzleAndSolution, wrapper_solutions: list[Program]
    ) -> DelegatedPuzzleAndSolution:
        if len(wrapper_solutions) != len(self.required_wrappers):
            raise ValueError("Number of wrapper solutions does not match number of required wrappers")

        for wrapper, wrapper_solution in zip(reversed(self.required_wrappers), reversed(wrapper_solutions)):
            delegated_puzzle_and_solution = DelegatedPuzzleAndSolution(
                puzzle=ADD_DPUZ_WRAPPER.curry(wrapper.puzzle(UNUSED_NONCE), delegated_puzzle_and_solution.puzzle),
                solution=Program.to([wrapper_solution, delegated_puzzle_and_solution.solution]),
            )

```

**File:** chia/_tests/clvm/test_restrictions.py (L45-53)
```python
@pytest.mark.anyio
async def test_dpuz_validator_stack_restriction(cost_logger: CostLogger) -> None:
    async with sim_and_client() as (sim, client):
        restriction = ValidatorStackRestriction(required_wrappers=[EasyDPuzWrapper(), EasyDPuzWrapper()])
        pwr = PuzzleWithRestrictions(nonce=0, restrictions=[restriction], puzzle=ACSMember())

        # Farm and find coin
        await sim.farm_block(pwr.puzzle_hash())
        coin = (await client.get_coin_records_by_puzzle_hashes([pwr.puzzle_hash()], include_spent_coins=False))[0].coin
```

**File:** chia/_tests/clvm/test_custody_architecture.py (L394-411)
```python
@pytest.mark.anyio
async def test_restriction_layer(cost_logger: CostLogger) -> None:
    """
    This tests the capabilities of the optional restriction layer placed on inner puzzles.
    """
    async with sim_and_client() as (sim, client):
        pwr = PuzzleWithRestrictions(
            nonce=0,
            restrictions=[ACSMemberValidator(), ACSMemberValidator(), ACSDPuzValidator(), ACSDPuzValidator()],
            puzzle=ACSMember(),
        )

        # Farm coin with puzzle inside
        await sim.farm_block(pwr.puzzle_hash())
        pwr_coin = (await client.get_coin_records_by_puzzle_hashes([pwr.puzzle_hash()], include_spent_coins=False))[
            0
        ].coin

```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L720-727)
```python
@pytest.mark.anyio
async def test_block_cost_exceeds_max(zero_mempool_manager: MempoolManager) -> None:
    conditions = []
    for i in range(2400):
        conditions.append([ConditionOpcode.CREATE_COIN, IDENTITY_PUZZLE_HASH, i])
    sb = spend_bundle_from_conditions(conditions)
    with pytest.raises(ValidationError, match="BLOCK_COST_EXCEEDS_MAX"):
        await zero_mempool_manager.pre_validate_spendbundle(sb)
```
