### Title
Incomplete Message-Condition Blacklist in `SendMessageBanned` Custody Restriction Allows Bypass via `RECEIVE_MESSAGE` - (File: `chia/wallet/puzzles/custody/send_message_banned.clsp`)

### Summary
The `SendMessageBanned` custody restriction is designed to strip a delegated puzzle's ability to emit any message-based coordination condition from a restricted coin (used e.g. in the PlotNFT v2 waiting-room exit path and general `PuzzleWithRestrictions` vaults). Its enforcement puzzle only blacklists the `SEND_MESSAGE` opcode (66) and never checks for `RECEIVE_MESSAGE` (67), so a restricted coin can still participate in the exact same cross-coin, same-bundle communication primitive it was meant to be denied, mirroring the MacCMS10 pattern of an incomplete function/opcode blacklist that omits functionally-equivalent primitives.

### Finding Description
`send_message_banned.clsp` implements the entire "ban" as a single linear scan: [1](#0-0) 

```
(defun check_conditions (conditions return)
    (if conditions
        (if (= (f (f conditions)) SEND_MESSAGE)
            (x)
            (check_conditions (r conditions) return)
        )
        return
    )
  )
```

This checks only that no condition's opcode equals `SEND_MESSAGE` (66). `condition_codes.clib`/`chia/types/condition_opcodes.py` defines `SEND_MESSAGE = 66` and `RECEIVE_MESSAGE = 67` as a paired primitive: [2](#0-1)  and [3](#0-2) . Consensus enforces that every `SEND_MESSAGE` in a spend bundle is matched by a corresponding `RECEIVE_MESSAGE` from some other coin in the same bundle (or vice versa), failing with `MESSAGE_NOT_SENT_OR_RECEIVED` otherwise, as exercised in `test_message_conditions`: [4](#0-3) . Because pairing is bundle-wide rather than per-coin, a restricted coin can legally emit `RECEIVE_MESSAGE` (opcode 67) while an unrestricted coin in the same bundle emits the matching `SEND_MESSAGE`. `send_message_banned.clsp` never inspects for opcode 67, so this exact same "coordinate atomically with another coin via a message condition" capability the restriction is supposed to deny remains fully available through the unblocked half of the pair.

The restriction is wired into the custody stack via `SendMessageBanned`, whose only enforcement artifact is this puzzle: [5](#0-4) . It is applied as a required wrapper on delegated-puzzle output through `ValidatorStackRestriction.modify_delegated_puzzle_and_solution`, which curries the child delegated puzzle with `ADD_DPUZ_WRAPPER` and forces its output through the wrapper puzzle before conditions are accepted: [6](#0-5) . The existing test suite only validates the blocked (`SEND_MESSAGE`) half of the pair and never asserts that `RECEIVE_MESSAGE` is also rejected: [7](#0-6) .

The same restriction is used in production PlotNFT v2 pool-exit logic to prevent a message condition from being smuggled into the "leave pool" spend, as shown by the negative test asserting that a `SEND_MESSAGE` in the exit delegated puzzle is rejected with `GENERATOR_RUNTIME_ERROR`: [8](#0-7) . No equivalent test (or enforcement) exists for a `RECEIVE_MESSAGE` condition placed in that same exit path.

### Impact Explanation
Any coin (wallet, vault member, or plotnft owner) protected by the `SendMessageBanned` restriction can still use the message-condition channel — just from the "receive" side — to atomically coordinate its restricted spend with an arbitrary unrestricted coin in the same spend bundle. This defeats the intended isolation guarantee of the restriction: an attacker who controls (or colludes with) any other coin in the bundle can use the paired `SEND_MESSAGE`/`RECEIVE_MESSAGE` mechanism to pass authenticated, atomically-verified data (parent id, puzzle hash, amount commitments) into the "banned" coin's spend, achieving the same cross-coin signaling/coordination the restriction exists to block. Depending on how a given vault composes this restriction with other members/restrictions (e.g., timelocks, fixed destinations), this can be leveraged to coordinate otherwise-prevented fund movement out of a restricted custody path, i.e., unauthorized coin movement from a vault whose security model explicitly relies on this puzzle to sever message-based coordination.

### Likelihood Explanation
This is directly reachable by any wallet user or offer/vault participant who can submit a spend bundle: no privileged network position, malicious peer, or consensus-level control is required — only the ability to construct a `WalletSpendBundle` containing the restricted coin's spend plus a second, cooperating coin spend that emits the paired condition. The bypass requires only crafting a `RECEIVE_MESSAGE` condition (with matching commitments from the cooperating coin's `SEND_MESSAGE`) inside the delegated puzzle output, which is well within normal CLVM puzzle-solution authoring capability already demonstrated by existing test helpers (`MessageParticipant`, `SendMessage` in `chia/wallet/conditions.py`).

### Recommendation
Update `send_message_banned.clsp` (and any Rust/Python mirrors of this check) to blacklist both `SEND_MESSAGE` (66) and `RECEIVE_MESSAGE` (67), i.e., change the opcode comparison to reject any condition whose opcode is either paired message primitive, not just 66. Add a negative test mirroring `test_send_message_banned` that asserts a lone `RECEIVE_MESSAGE` in the restricted coin's output is also rejected, and re-audit other allow/deny-list style custody restrictions (e.g., `FixedCreateCoinDestinations`) for similarly incomplete opcode coverage.

### Proof of Concept
1. Build a `PuzzleWithRestrictions` with `restrictions=[ValidatorStackRestriction(required_wrappers=[SendMessageBanned()])]` as in `test_send_message_banned` (`chia/_tests/clvm/test_restrictions.py:241`).
2. Instead of the delegated puzzle emitting `SendMessage(...)` (which is correctly rejected), have it emit `ReceiveMessage(...)` with a mode/message/coin-id commitment matching a `SendMessage(...)` condition emitted by a second, unrestricted coin included in the same `WalletSpendBundle`.
3. Submit the bundle via `client.push_tx(...)`. Because `send_message_banned.clsp` only checks for opcode 66, the restricted coin's output passes the wrapper check; consensus-level message pairing succeeds because the paired `SEND_MESSAGE` exists elsewhere in the bundle, so the whole bundle is admitted — demonstrating that the restriction failed to prevent the coin from participating in message-based cross-coin coordination.

### Citations

**File:** chia/wallet/puzzles/custody/send_message_banned.clsp (L1-19)
```text
; A puzzle that enforces the lack of SEND_MESSAGE conditions in the condition set
;
; Intended for use in validating the output of a delegated puzzle
(mod (Conditions)

  (include condition_codes.clib)

  (defun check_conditions (conditions return)
    (if conditions
        (if (= (f (f conditions)) SEND_MESSAGE)
            (x)
            (check_conditions (r conditions) return)
        )
        return
    )
  )

  (check_conditions Conditions Conditions)
)
```

**File:** chia/wallet/puzzles/condition_codes.clib (L37-39)
```text
  ; mask message ...
  (defconstant SEND_MESSAGE 66)
  (defconstant RECEIVE_MESSAGE 67)
```

**File:** chia/types/condition_opcodes.py (L37-39)
```python
    SEND_MESSAGE = bytes([66])
    RECEIVE_MESSAGE = bytes([67])

```

**File:** chia/_tests/core/full_node/test_conditions.py (L439-465)
```python
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "conds, expected",
        [
            ('(66 0x3f "foobar" {coin}) (67 0x3f "foobar" {coin})', None),
            ('(66 0x3f "foobar" {coin}) (67 0x3f "foo" {coin})', Err.MESSAGE_NOT_SENT_OR_RECEIVED),
            ('(66 0x39 "foo" {coin})', Err.COIN_AMOUNT_EXCEEDS_MAXIMUM),
            ('(67 0x0f "foo" {coin})', Err.COIN_AMOUNT_EXCEEDS_MAXIMUM),
            ('(66 0x3f "foo" {coin}) (67 0x27 "foo" {coin})', Err.MESSAGE_NOT_SENT_OR_RECEIVED),
            ('(66 0x27 "foo" {coin}) (67 0x3f "foo" {coin})', Err.MESSAGE_NOT_SENT_OR_RECEIVED),
            ('(66 0 "foo") (67 0 "foo")', None),
            ('(66 0 "foobar") (67 0 "foo")', Err.MESSAGE_NOT_SENT_OR_RECEIVED),
            ('(66 0x09 "foo" 250000000000) (67 0x09 "foo" 250000000000)', None),
            ('(66 -1 "foo")', Err.INVALID_MESSAGE_MODE),
            ('(67 -1 "foo")', Err.INVALID_MESSAGE_MODE),
            ('(66 0x40 "foo")', Err.INVALID_MESSAGE_MODE),
            ('(67 0x40 "foo")', Err.INVALID_MESSAGE_MODE),
        ],
    )
    async def test_message_conditions(
        self, bt: BlockTools, consensus_mode: ConsensusMode, conds: str, expected: Err | None
    ) -> None:
        blocks = await initial_blocks(bt)
        coin = find_reward_coin(blocks[-2], EASY_PUZZLE_HASH)
        conditions = Program.to(assemble("(" + conds.format(coin="0x" + coin.name().hex()) + ")"))

        await check_conditions(bt, conditions, expected_err=expected)
```

**File:** chia/wallet/puzzles/custody/restrictions.py (L48-57)
```python
@dataclass(kw_only=True, frozen=True)
class SendMessageBanned:
    def memo(self, nonce: int) -> Program:
        return Program.to(None)

    def puzzle(self, nonce: int) -> Program:
        return SEND_MESSAGE_BANNED

    def puzzle_hash(self, nonce: int) -> bytes32:
        return self.puzzle(nonce).get_tree_hash()  # TODO: optimize
```

**File:** chia/wallet/puzzles/custody/restriction_utilities.py (L51-62)
```python
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

**File:** chia/_tests/clvm/test_restrictions.py (L241-284)
```python
@pytest.mark.anyio
async def test_send_message_banned(cost_logger: CostLogger) -> None:
    async with sim_and_client() as (sim, client):
        restriction = ValidatorStackRestriction(required_wrappers=[SendMessageBanned()])
        pwr = PuzzleWithRestrictions(nonce=0, restrictions=[restriction], puzzle=ACSMember())

        # Farm and find coin
        await sim.farm_block(pwr.puzzle_hash())
        coin = (await client.get_coin_records_by_puzzle_hashes([pwr.puzzle_hash()], include_spent_coins=False))[0].coin

        # Attempt to send a message
        send_message_dpuz = DelegatedPuzzleAndSolution(
            puzzle=Program.to(
                (
                    1,
                    [
                        SendMessage(
                            bytes32.zeros,
                            sender=MessageParticipant(parent_id_committed=bytes32.zeros),
                            receiver=MessageParticipant(parent_id_committed=bytes32.zeros),
                        ).to_program()
                    ],
                )
            ),
            solution=Program.to(None),
        )
        wrapped_dpuz = restriction.modify_delegated_puzzle_and_solution(send_message_dpuz, [Program.to(None)])
        escape_attempt = WalletSpendBundle(
            [
                make_spend(
                    coin,
                    pwr.puzzle_reveal(),
                    pwr.solve(
                        [],
                        [Program.to([send_message_dpuz.puzzle.get_tree_hash()])],
                        Program.to(None),
                        wrapped_dpuz,
                    ),
                )
            ],
            G2Element(),
        )
        result = await client.push_tx(escape_attempt)
        assert result == (MempoolInclusionStatus.FAILED, Err.GENERATOR_RUNTIME_ERROR)
```

**File:** chia/_tests/pools/test_plotnft_v2_drivers.py (L192-216)
```python
        # # Attempt to make a message while leaving
        message_dpuz_and_solution = DelegatedPuzzleAndSolution(
            puzzle=ACS,
            solution=Program.to(
                [
                    plotnft.exit_to_waiting_room_condition().to_program(),
                    SendMessage(
                        bytes32.zeros,
                        sender=MessageParticipant(parent_id_committed=bytes32.zeros),
                        receiver=MessageParticipant(parent_id_committed=bytes32.zeros),
                    ).to_program(),
                ]
            ),
        )
        singing_info = plotnft.modify_delegated_puzzle_and_solution(message_dpuz_and_solution)
        coin_spends = plotnft.exit_to_waiting_room(message_dpuz_and_solution)
        result = await sim_client.push_tx(
            WalletSpendBundle(
                coin_spends,
                user_sk.sign(
                    singing_info.puzzle.get_tree_hash() + plotnft.coin.name() + sim.defaults.AGG_SIG_ME_ADDITIONAL_DATA
                ),
            )
        )
        assert result == (MempoolInclusionStatus.FAILED, Err.GENERATOR_RUNTIME_ERROR)
```
