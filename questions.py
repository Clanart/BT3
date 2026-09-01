import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'chainwayxyz/clementine'
# todo: the name of the repository
REPO_NAME = 'clementine'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # LENS: FROM A USER-SHAPED BYTE TO 10 BTC LEAVING THE N-OF-N VAULT.
    # Clementine is a BitVM2 two-way peg. Everything it does is decided by bytes an
    # ordinary user chooses: a deposit output they fund on Bitcoin, a `withdraw` call and
    # a withdrawal UTXO they register in the Citrea Bridge contract, a Schnorr signature
    # they hand to operators, an OP_RETURN / witness / annex they put in a Bitcoin tx,
    # and a request they send to the public aggregator gRPC port. Those bytes end in
    # three places: an N-of-N presigned move-to-vault UTXO, a Reimburse tx that pays an
    # operator 10 BTC out of that vault, and a bridge-circuit `journal_hash` that decides
    # whether an operator's collateral is burned. Every file below sits on the path
    # between one of those inputs and one of those outcomes. A file belongs here only if
    # a custody, attribution or proof binding must hold across it.
    # =================================================================================

    # -- The public front door: gRPC surface, who may call it, and how bytes are parsed --
    # `Interceptors::Noop` is installed whenever `client_verification` is false, and the
    # aggregator is the entity users are meant to reach. `parser::*` turns protobuf into
    # `DepositData`, outpoints, sighash types and addresses with no other gate.
    "core/src/servers.rs",
    "core/src/rpc/interceptors.rs",
    "core/src/rpc/aggregator.rs",
    "core/src/rpc/verifier.rs",
    "core/src/rpc/operator.rs",
    "core/src/rpc/mod.rs",
    "core/src/rpc/parser/mod.rs",
    "core/src/rpc/parser/verifier.rs",
    "core/src/rpc/parser/operator.rs",
    "core/src/rpc/ecdsa_verification_sig.rs",
    "core/src/rpc/error.rs",

    # -- Deposit acceptance: what a user must fund before N-of-N will presign a vault ----
    # `Verifier::is_deposit_valid`, `DepositData::get_deposit_scripts`, `BaseDepositScript`
    # / `ReplacementDepositScript` / `Multisig`, and the move-to-vault construction.
    "core/src/verifier.rs",
    "core/src/deposit.rs",
    "core/src/builder/script.rs",
    "core/src/builder/address.rs",
    "core/src/builder/transaction/mod.rs",
    "core/src/builder/transaction/creator.rs",
    "core/src/builder/transaction/txhandler.rs",
    "core/src/builder/transaction/input.rs",
    "core/src/builder/transaction/output.rs",
    "core/src/builder/transaction/sign.rs",
    "core/src/builder/transaction/deposit_signature_owner.rs",
    "core/src/builder/sighash.rs",

    # -- Withdrawal, payout and reimbursement: where BTC actually leaves ------------------
    # `Operator::withdraw` verifies the user's signature with the sighash type the user
    # supplied; `create_payout_txhandler` writes the operator xonly pk into an OP_RETURN;
    # `sign_optimistic_payout` spends the vault directly on N-of-N partial signatures.
    "core/src/operator.rs",
    "core/src/aggregator.rs",
    "core/src/builder/transaction/operator_reimburse.rs",
    "core/src/builder/transaction/operator_collateral.rs",
    "core/src/builder/transaction/operator_assert.rs",
    "core/src/builder/transaction/challenge.rs",
    "core/src/musig2.rs",
    "core/src/actor.rs",
    "core/src/bitvm_client.rs",

    # -- Citrea state the bridge trusts: deposits, withdrawal UTXOs, LCP, storage proofs --
    "core/src/citrea.rs",
    "core/src/task/payout_checker.rs",
    "core/src/task/lcp_syncer.rs",
    "core/src/task/tx_sender.rs",
    "core/src/task/manager.rs",
    "core/src/task/mod.rs",

    # -- Chain observation and the state machine that fires challenges and disproves ------
    "core/src/bitcoin_syncer.rs",
    "core/src/extended_bitcoin_rpc.rs",
    "core/src/header_chain_prover.rs",
    "core/src/states/mod.rs",
    "core/src/states/kickoff.rs",
    "core/src/states/round.rs",
    "core/src/states/matcher.rs",
    "core/src/states/event.rs",
    "core/src/states/context.rs",
    "core/src/states/task.rs",
    "core/src/builder/block_cache.rs",

    # -- Persisted protocol truth ---------------------------------------------------------
    "core/src/database/mod.rs",
    "core/src/database/verifier.rs",
    "core/src/database/operator.rs",
    "core/src/database/aggregator.rs",
    "core/src/database/bitcoin_syncer.rs",
    "core/src/database/header_chain_prover.rs",
    "core/src/database/state_machine.rs",
    "core/src/database/wrapper.rs",

    # -- Protocol constants, keys and shared helpers --------------------------------------
    "core/src/config/mod.rs",
    "core/src/config/protocol.rs",
    "core/src/config/env.rs",
    "core/src/constants.rs",
    "core/src/utils.rs",
    "core/src/encryption.rs",
    "core/src/compatibility.rs",
    "core/src/tx_sender_ext.rs",
    "core/src/tx_sender_queue.rs",
    "crates/clementine-primitives/src/lib.rs",
    "crates/clementine-config/src/protocol.rs",
    "crates/clementine-config/src/grpc.rs",
    "crates/clementine-utils/src/address.rs",
    "crates/clementine-utils/src/sign.rs",
    "crates/clementine-extended-rpc/src/client.rs",
    "crates/clementine-extended-rpc/src/retry.rs",

    # -- Getting deadline-bound transactions confirmed (challenge / disprove / timeout) ----
    "crates/clementine-tx-sender/src/lib.rs",
    "crates/clementine-tx-sender/src/rbf.rs",
    "crates/clementine-tx-sender/src/cpfp.rs",
    "crates/clementine-tx-sender/src/confirmations.rs",
    "crates/clementine-tx-sender/src/nonstandard.rs",
    "crates/clementine-tx-sender/src/signer.rs",
    "crates/clementine-tx-sender/src/client.rs",
    "crates/clementine-tx-sender/src/db/tx_sender.rs",
    "crates/clementine-tx-sender/src/db/citrea.rs",
    "crates/clementine-tx-sender/src/citrea/sync.rs",
    "crates/clementine-tx-sender/src/citrea/reveal_scripts.rs",
    "crates/clementine-tx-sender/src/citrea/data_serialization.rs",
    "crates/clementine-tx-sender/src/jsonrpc/server.rs",
    "crates/tx-sender-types/src/clementine.rs",
    "crates/tx-sender-types/src/citrea.rs",

    # -- The circuits: what a Groth16 proof is actually allowed to claim -------------------
    "circuits-lib/src/bridge_circuit/mod.rs",
    "circuits-lib/src/bridge_circuit/spv.rs",
    "circuits-lib/src/bridge_circuit/merkle_tree.rs",
    "circuits-lib/src/bridge_circuit/storage_proof.rs",
    "circuits-lib/src/bridge_circuit/lc_proof.rs",
    "circuits-lib/src/bridge_circuit/groth16.rs",
    "circuits-lib/src/bridge_circuit/groth16_verifier.rs",
    "circuits-lib/src/bridge_circuit/transaction.rs",
    "circuits-lib/src/bridge_circuit/structs.rs",
    "circuits-lib/src/bridge_circuit/constants.rs",
    "circuits-lib/src/header_chain/mod.rs",
    "circuits-lib/src/header_chain/mmr_guest.rs",
    "circuits-lib/src/header_chain/mmr_native.rs",
    "circuits-lib/src/work_only/mod.rs",
    "circuits-lib/src/common/zkvm.rs",
    "circuits-lib/src/common/hashes.rs",
    "circuits-lib/src/common/constants.rs",
    "bridge-circuit-host/src/bridge_circuit_host.rs",
    "bridge-circuit-host/src/structs.rs",
    "bridge-circuit-host/src/utils.rs",
    "bridge-circuit-host/src/lib.rs",

    # =================================================================================
    # NOT IN THIS VARIANT:
    # * core/src/test/**, **/tests.rs, **/test.rs, test_utils.rs, mock_zkvm.rs,
    #   client_mock.rs, circuits-lib/**/tests/** - tests, fixtures and mocks.
    # * core/src/rpc/clementine.rs and bridge-circuit-host/src/seal_format.rs - generated.
    # * **/build.rs, risc0-circuits/**/guest/src/main.rs (4-24 line shims), elfs/,
    #   *.toml, *.md, docs/**, devops/**, scripts/**, migrations/**, *.sql - build,
    #   configuration, documentation and generated artefacts.
    # * core/src/main.rs, core/src/cli.rs, core/src/bin/cli.rs, metrics.rs, tracing.rs -
    #   process startup, operator tooling and telemetry, no custody decision.
    # =================================================================================
]


target_scopes = [
    "Critical. THE OPERATOR WHO GETS PAID IS NAMED BY AN OP_RETURN ANYONE CAN REWRITE. `create_payout_txhandler` puts `operator_xonly_pk.serialize()` in output 2 while the withdrawer's own signature over input 0 is `SinglePlusAnyoneCanPay` (`Operator::withdraw` -> `SECP.verify_schnorr`), which commits to input 0 and output 0 only. `Verifier::update_finalized_payouts` then attributes the withdrawal by `get_first_op_return_output` + `parse_op_return_data`, `PayoutCheckerTask::run_once` reads it back via `get_first_unhandled_payout_by_operator_xonly_pk`, and `Verifier::is_kickoff_malicious` calls a kickoff malicious when the stored xonly pk is `None` or differs. Show an unprivileged third party who copies the broadcast payout's input 0 + output 0, attaches a different or non-parsable OP_RETURN, and gets it mined first - then follow it to an honest operator that fronted 10 BTC and can never send a valid Reimburse, or to a challenge and Disprove that burns its collateral. Binding: the operator xonly pk recorded for withdrawal index i == the party whose funds paid that payout output.",

    "Critical. THE BRIDGE CIRCUIT NEVER CHECKS THAT ANYONE WAS PAID. `bridge_circuit` asserts only that `input.payout_spv.transaction.input[payout_input_index].previous_output` equals the `(user_wd_outpoint, vout)` returned by `verify_storage_proofs`, and that an OP_RETURN exists; `payout_input_index` is read straight from `BridgeCircuitInput` with no bound check against `transaction.input.len()`, and no output value, no destination script and no `bridge_amount` is ever constrained before `deposit_constant` and `journal_hash` are committed. Show a payout transaction that spends the registered withdrawal UTXO while paying the withdrawer nothing (or paying it all to fees / to the prover), and follow the resulting valid journal through Assert, ChallengeTimeout and Reimburse to 10 BTC leaving a move-to-vault UTXO. Binding: the value the withdrawer receives in the transaction the circuit accepts == the withdrawal the Citrea Bridge contract recorded at that index.",

    "Critical. ONE PAYOUT, TWO REIMBURSEMENTS - INDEX CONFLATION. `CitreaClient::get_storage_proof` and `verify_storage_proofs` derive the withdrawal UTXO from `keccak256(UTXOS_STORAGE_INDEX) + index*2` and the move txid from `keccak256(DEPOSIT_STORAGE_INDEX) + index`, with `storage_proof.index` supplied by the prover and `index * 2` computed on a `u32`; `Aggregator::optimistic_payout` and `Verifier::sign_optimistic_payout` reuse the same `deposit_id` for both `get_move_to_vault_txid_from_citrea_deposit` and `get_withdrawal_utxo_from_citrea_withdrawal`, and `update_finalized_payouts` keys payouts by withdrawal utxo alone. Show one on-chain payout transaction spending a single withdrawal UTXO being converted into two settled claims - two indices, or an optimistic payout plus a kickoff reimbursement, or an index whose `*2` wraps - so more BTC leaves the vault than was ever fronted. Binding: the number of vault UTXOs spent for withdrawal index i == 1, and each equals exactly one payout actually fronted.",

    "Critical. THE PERSON BEING PAID CHOOSES THE SIGHASH TYPE. `Operator::withdraw` computes `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` and `Aggregator::optimistic_payout` computes `calculate_pubkey_spend_sighash(0, input_signature.sighash_type)` using the `taproot::Signature` the caller supplied, so `SinglePlusAnyoneCanPay` is a comment in an error string, not a check; `Verifier::sign_optimistic_payout` re-signs from that same signature and only bounds `output_amount <= bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT`, while `Operator::is_profitable` returns `true` outright when the user's input value exceeds the withdrawal amount. Show a caller-supplied sighash flag (NONE, ALL, ANYONECANPAY combinations) or an amount pair that lets the withdrawal signature be replayed into a different transaction, invalidates the funded payout after `fund_raw_transaction` adds operator inputs, or moves vault value into fees. Binding: the sighash type verified against the user's key == a flag that commits the payout output the operator is reimbursed for.",

    "Critical. WHAT N-OF-N AGREES TO PRESIGN. `Verifier::is_deposit_valid` compares the security council, actor uniqueness, watchtower counts, the on-chain `script_pubkey` against `create_taproot_address(get_deposit_scripts(..))`, `value == bridge_amount` and `block_height >= start_height` - and nothing else. It never asks whether that deposit outpoint was already moved into a vault, whether the deposit is still unspent, or, for `DepositType::ReplacementDeposit`, whether `old_move_txid` names a real prior move tx that the `Multisig::from_security_council` path actually spent. `DepositData::eq` compares only the outpoint, type, security council and sorted actor sets, and `get_nofn_xonly_pk` caches a key derived from the aggregator-supplied verifier list. Show an unprivileged depositor whose crafted `DepositParams` gets a second move-to-vault presigned for one funded outpoint, gets a self-funded replacement deposit accepted for a fabricated `old_move_txid`, or shifts the derived `nofn_xonly_pk`. Binding: a move txid the verifiers sign == exactly one unspent `bridge_amount` deposit output carrying the exact scripts, counted once by the Citrea Bridge contract.",

    "Critical. THE ENTITY USERS ARE TOLD TO CALL HAS NO CALLER CHECK. `create_grpc_server` installs `Interceptors::Noop` whenever `config.client_verification` is false - the documented state for the aggregator, which only logs `tracing::warn!` when verification IS enabled - and `only_aggregator_and_self` is the sole thing that makes `is_internal` mean anything. On that open port `ClementineAggregator` exposes `Setup`, `NewDeposit`, `Withdraw`, `OptimisticPayout`, `InternalSendTx`, `SendMoveToVaultTx` and `InternalGetEmergencyStopTx`. Show an internet-reachable request on that port that broadcasts a transaction of the attacker's choosing, re-runs setup, extracts an emergency-stop transaction, or drives verifiers into a signing session, with no certificate and no key. Binding: a caller that reaches an aggregator method mutating protocol state or spending a bridge UTXO == a party holding the aggregator's certificate.",

    "Critical. A SECNONCE THAT SIGNS TWICE IS A LEAKED KEY. `NonceSession` / `AllSessions` hand out `nonce_session_id`s and `Verifier::sign_optimistic_payout` does `session.nonces.pop()` for a sighash built from caller-supplied `input_outpoint`, `output_script_pubkey`, `output_amount` and `input_signature`, while `Verifier::nonce_gen`, `deposit_sign` and `deposit_finalize` stream nonces for a deposit the caller named; `remove_oldest_session`, `get_new_unused_id` and `total_sessions_byte_size` decide which session survives, and `musig2::partial_sign` is called with whatever `agg_nonce` the aggregator passes. Show two requests an unprivileged caller can send through the public aggregator that cause one verifier to produce two partial signatures over different messages under the same secnonce or aggregated nonce, or that make a nonce from one session serve another deposit. Binding: each secnonce popped from a `NonceSession` == exactly one message ever signed under it.",

    "Critical. SPV IS ONLY AS TIGHT AS ITS PATH LENGTH. `SPV::verify` takes `mid_state_txid`, feeds it to `BlockInclusionProof::get_root`, compares against `block_header.merkle_root` and then `MMRGuest::verify_proof`; the leaf index, the number of siblings and the MMR subroot selection all come from the untrusted `BridgeCircuitInput`, while `BitcoinMerkleTree::new` and `new_mid_state` only panic on duplicates at construction time, which never runs in the guest. `CircuitTransaction::mid_state_txid` hashes version, inputs, outputs and locktime with no witness, and `BorshDeserialize for CircuitTransaction` reconstructs a transaction from attacker bytes. Show a proof-carrying transaction that is accepted as included in a canonical block while it was never mined there - a forged path depth, a colliding mid-state preimage, or an MMR proof against a subroot the header chain never committed. Binding: the transaction `payout_spv` proves == a transaction in a block whose hash the header-chain proof committed to the MMR.",

    "Critical. THE KICKOFF COMMITS TO A BLOCK HASH THAT CAN STOP BEING TRUE. `PayoutCheckerTask::run_once` reads the payout block hash straight from `update_finalized_payouts`/`get_block_info_from_hash` and `Operator::handle_finalized_payout` commits `payout_tx_blockhash.last_20_bytes()` into the kickoff witness by WOTS before any depth requirement; `Verifier::is_kickoff_malicious` re-derives the same 20 bytes from its own DB, and `bridge_circuit` requires `light_client_circuit_output.latest_da_state.block_hash == payout_spv.block_header.compute_block_hash()`. Show an unprivileged attacker who replaces or delays the payout transaction (RBF, a conflicting spend of the same withdrawal UTXO, or landing it in a block that is later reorged out - see `bitcoin_syncer` and `handle_finalized_block`) so that the hash an honest operator has already committed no longer holds, then follow it to `handle_kickoff`, Challenge and Disprove burning that operator's collateral. Binding: the block hash committed once and irrevocably in the kickoff == the block containing the payout in the chain the header-chain proof will later prove.",

    "Critical. THE MISSING BINDING - what nobody built. Nothing in this repository records the identity of the party that actually funded a payout output; attribution is an OP_RETURN in a transaction the withdrawer's own `SinglePlusAnyoneCanPay` signature lets anyone rebuild. Nothing marks a withdrawal UTXO, a deposit outpoint or a payout transaction as consumed across the deposit index, the withdrawal index and the storage-proof index. No code path re-checks, at Reimburse time, that value equal to the withdrawal ever reached the withdrawer. Identify the FIRST point at which a byte an unprivileged user chose - a deposit script, a registered withdrawal outpoint, a Schnorr signature and its sighash flag, an OP_RETURN, a witness or annex, a `payout_input_index`, a `storage_proof.index`, or a request on the open aggregator port - becomes a presigned vault spend, a settled payout attribution, a committed WOTS value or a Groth16 journal with no independent party ever re-deriving it. Prove it with one `cargo test` under `core/src/test` or `circuits-lib` asserting both the value used and the value that should have authorised it, and show that once they diverge nothing in the protocol reconciles them.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate bridge-custody audit questions for one Clementine target.

    ```
    target_file format:
    "'File Name: core/src/verifier.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate custody and proof-soundness security audit questions for this exact
    Clementine target:

    {target_file}

    Project focus:
    Clementine is Citrea's BitVM2 two-way peg. Untrusted bytes enter through four doors:
    a deposit output a user funds on Bitcoin (`Verifier::is_deposit_valid`), a `withdraw`
    call plus a withdrawal UTXO and a Schnorr signature a user registers on Citrea and
    hands to operators (`Operator::withdraw`, `Aggregator::optimistic_payout`), any
    Bitcoin transaction an attacker can broadcast (payout replacements, OP_RETURN,
    witness, annex, reorgs seen by `bitcoin_syncer` and the state machines), and the
    aggregator's gRPC port, which runs `Interceptors::Noop` unless `client_verification`
    is on. Those bytes end in three places: an N-of-N presigned move-to-vault UTXO
    holding `bridge_amount`, a Reimburse tx paying an operator out of that vault, and a
    bridge-circuit `journal_hash` that decides whether an operator's collateral is burned.
    Anything that moves vault value, credits a reimbursement, or makes an honest party
    disprovable without the protocol re-deriving the fact independently is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (module, struct, enum, fn, const, field) as they appear in the file.
    * EVERY question must close on a binding that must hold across a call. State it explicitly
      as an equality between two named values. Narrative questions are rejected.
    * Attacker is unprivileged only: anyone who can broadcast Bitcoin transactions and pay
      fees, deposit into the bridge, call `withdraw` on the Citrea Bridge contract, choose
      the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag, an
      OP_RETURN, a script, a witness or an annex, and send requests to the aggregator's
      public gRPC port.
    * Attacker is NOT a verifier, operator, watchtower, aggregator, security council member,
      Citrea sequencer or batch prover. They hold no verifier key share, no operator
      collateral, no aggregator or client TLS certificate, no `security_council` key. No
      malicious peer or node, no key compromise, no majority hashrate, no TLS interception,
      no local or physical access, no compromised dependency, no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Tests, mocks and fixtures (`core/src/test/**`, `**/tests.rs`, `**/test.rs`,
        `test_utils.rs`, `mock_zkvm.rs`), generated files (`core/src/rpc/clementine.rs`,
        `seal_format.rs`, `**/build.rs`, `elfs/`), `*.toml`, `*.md`, `docs/**`,
        `devops/**`, `scripts/**` and migrations are OUT OF SCOPE.
      - Denial of service, rate limiting, retry/backoff, queue depth, resource exhaustion,
        unbounded collections, memory hygiene and log volume are OUT OF SCOPE.
      - Any scenario needing a dishonest verifier, operator, watchtower, aggregator,
        security council or Citrea sequencer is OUT OF SCOPE, as is anything that only
        harms the attacker's own funds.
      - Defects in third-party crates (bitcoin, risc0, bitvm, secp256k1, alloy, sqlx,
        tonic) with no exploit path through this repository's own code are OUT OF SCOPE,
        as are Citrea contract or light-client-circuit bugs.
      - Also excluded: leaked keys, best-practice notes, feature requests, missing headers,
        and theoretical findings with no demonstration.
      - A weakness in this repository that manipulates a third-party crate into unsafe
        behaviour remains fully in scope.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: BTC leaving a move-to-vault UTXO without a matching fronted withdrawal;
      an operator reimbursed for a payout it never funded; an honest operator that funded a
      payout being permanently unable to be reimbursed; an honest operator's collateral
      burned via Challenge/Disprove/ChallengeNACK; a move-to-vault UTXO permanently frozen;
      a bridge, header-chain or work-only proof accepted for a claim that is false, or a
      true claim made unprovable; N-of-N partial signatures produced for a spend no Citrea
      withdrawal authorises; a verifier secret key or secnonce recoverable.
      High: an unauthenticated call that mutates protocol state or broadcasts a bridge
      transaction; a deadline-bound challenge, disprove or timeout transaction made
      unconfirmable by attacker-shaped chain data; leakage of protocol secrets or of a
      commitment (WOTS preimage) before its intended reveal.
    * Every question must be a concrete real-world scenario an unprivileged attacker can
      execute against a running Clementine deployment - a Bitcoin transaction they
      broadcast, a deposit they fund, a withdrawal they register, a signature they hand
      over, a gRPC request they send. No speculative resource-hygiene or memory questions.
    * A panic or error is a finding only when it makes an honest party disprovable, leaves
      vault funds unspendable, or lets an unauthorised spend through - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable by a `cargo test` under `core/src/test` or
      `circuits-lib` (regtest bitcoind, mocked `CitreaClientT`, or a direct circuit call),
      with no mainnet and no live Citrea.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are: the
      operator credited and the operator that paid, the amount owed and the amount received,
      the deposit presigned and the deposit minted, the block hash committed and the block
      hash proved, the message signed and the nonce used, the caller and the party the
      method is for.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a verifier, operator, watchtower, aggregator, security council or
      sequencer key, certificate or role.
    * A bug in a dependency, in the Citrea Bridge contract, or in the light client circuit
      with no reachable path through this repository.
    * Fee estimation, mempool policy, propagation timing, or an attacker burning only their
      own BTC or cBTC with no bridge value moved and no honest party harmed.
    * Findings only reproducible in tests, mocks, fixtures or generated files.

    Core bindings (each question must close on one):
    * CUSTODY: value leaving a move-to-vault UTXO == a withdrawal the Bridge contract
      recorded and that was actually fronted to that withdrawer.
    * ATTRIBUTION: the operator credited and reimbursed for withdrawal i == the party whose
      funds paid that payout, counted exactly once.
    * MINT AUTHORITY: a move txid the verifiers presign == one unspent `bridge_amount`
      deposit output with the exact required scripts.
    * PROOF SOUNDNESS: what `journal_hash`, `deposit_constant` and the WOTS commitments
      claim == what actually happened on Bitcoin and in Citrea state.
    * SLASHING TRUTH: an operator is challengeable or disprovable only when it actually
      deviated; an honest operator always retains a reachable Reimburse path.
    * CALLER AUTHORITY: a party reaching a state-changing or signing method == a party the
      interceptor and the protocol intend to allow.

    Each question must include:
    1. target struct/fn;
    2. attacker action (a concrete Bitcoin transaction, deposit, Citrea withdrawal,
       signature, or gRPC request with its fields);
    3. preconditions (paramset, deployment configuration, existing deposit or round state);
    4. call sequence through the code;
    5. the binding that breaks, written as an equality;
    6. scoped impact and whose BTC, collateral or proof is affected;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: struct_or_fn] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the binding BINDING_EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: cargo test PARAMETERS asserting CUSTODY, ATTRIBUTION, MINT_AUTHORITY, PROOF_SOUNDNESS, SLASHING_TRUTH, or CALLER_AUTHORITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a bridge-custody Clementine exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who can broadcast Bitcoin transactions and pay fees, deposit into the bridge, call `withdraw` on the Citrea Bridge contract, choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag, an OP_RETURN, a script, a witness or an annex, and send requests to the aggregator's public gRPC port. They are not a verifier, operator, watchtower, aggregator, security council member, Citrea sequencer or batch prover, and hold no key share, collateral or TLS certificate.
- Reject malicious peers or nodes, key compromise, majority hashrate, TLS interception, local or physical access, compromised dependencies and social engineering.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures (`core/src/test/**`, `**/tests.rs`, `**/test.rs`, `test_utils.rs`, `mock_zkvm.rs`), generated files (`core/src/rpc/clementine.rs`, `seal_format.rs`, `**/build.rs`, `elfs/`), `*.toml`, `*.md`, `docs/**`, `devops/**`, `scripts/**`, migrations; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; third-party crate, Citrea contract or light-client-circuit defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - BTC leaving a move-to-vault UTXO without a matching fronted withdrawal, an operator reimbursed for a payout it never funded, an honest operator permanently unable to be reimbursed, an honest operator's collateral burned, a move-to-vault UTXO permanently frozen, a false claim proved (or a true claim made unprovable) by the bridge, header-chain or work-only circuit, N-of-N partial signatures for an unauthorised spend, or a recoverable verifier secret or secnonce; High - an unauthenticated state-changing or broadcasting call, a deadline-bound challenge/disprove/timeout transaction made unconfirmable by attacker-shaped chain data, or premature disclosure of a protocol commitment.
- Focus on real impact: bridge value moving, a reimbursement credited to the wrong party, or an honest party losing collateral.

## Validate
- Write the binding the question claims is broken as an explicit equality between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's transaction, deposit, withdrawal, signature or gRPC request, and record every read and write of the deposit outpoint and scripts, `withdrawal_utxo`, `deposit_id`/`storage_proof.index`, `in_signature.sighash_type`, the payout OP_RETURN, the committed payout block hash, `payout_input_index`, the musig2 session and nonce, and the transaction that finally spends a bridge UTXO.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `Verifier::is_deposit_valid`, `Operator::is_profitable`, `SECP.verify_schnorr`, `only_aggregator_and_self`, `Verifier::is_kickoff_malicious`, `verify_storage_proofs`, `SPV::verify`, `lc_proof_verifier`, `total_work_and_watchtower_flags`, the presigned transaction graph or a database uniqueness constraint already prevents the divergence.
- State what the attacker gains or destroys per attempt and whether it is repeatable across deposits, withdrawals or operators.
- Require exact file/fn support and a reproducible `cargo test` proof with no mainnet and no live Citrea.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken binding as an equality, the code path, root cause, the attacker's exact transaction or request, exploit flow, and why existing guards fail]

### Impact Explanation
[What is spent, credited, frozen, proved or burned, which party, repeatability, blast radius across deposits and operators, matching severity category]

### Likelihood Explanation
[Preconditions, paramset and deployment configuration required, attacker cost in BTC and fees, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo test plan with the exact assertions on both sides of the binding]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Clementine claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A binding claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring a verifier, operator, watchtower, aggregator, security council, sequencer or batch prover role, key share, collateral or TLS certificate, a malicious peer or node, key compromise, majority hashrate, TLS interception, local or physical access, a compromised dependency, or social engineering.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures (`core/src/test/**`, `**/tests.rs`, `**/test.rs`, `test_utils.rs`, `mock_zkvm.rs`), generated files (`core/src/rpc/clementine.rs`, `seal_format.rs`, `**/build.rs`, `elfs/`), `*.toml`, `*.md`, `docs/**`, `devops/**`, `scripts/**`, migrations; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; third-party crate, Citrea contract or light-client-circuit defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - BTC leaving a move-to-vault UTXO without a matching fronted withdrawal, an operator reimbursed for a payout it never funded, an honest operator permanently unable to be reimbursed, an honest operator's collateral burned, a move-to-vault UTXO permanently frozen, a false claim proved or a true claim made unprovable by the circuits, N-of-N partial signatures for an unauthorised spend, or a recoverable verifier secret or secnonce; High - an unauthenticated state-changing or broadcasting call, a deadline-bound challenge/disprove/timeout transaction made unconfirmable by attacker-shaped chain data, or premature disclosure of a protocol commitment.
- Reject claims that depend on a deployment ignoring the documented configuration, or that only harm the attacker's own funds.
- Reject if the bug was already fixed, publicly disclosed, or is covered by an existing advisory or CHANGELOG entry for a supported version.
- Reject a divergence with no custody, attribution, proof or authorization boundary crossed.
- A valid report must be triggerable by an unprivileged attacker against a Clementine deployment running the current release.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, struct/fn, and line references.
2. The binding written explicitly as an equality, with both sides shown before and after.
3. Clear root cause: which unverified user field, which unbound index, which missing uniqueness check, which unconstrained sighash flag or output, which missing caller check causes the divergence.
4. Reachable exploit path: preconditions -> attacker Bitcoin transaction, deposit, withdrawal, signature or gRPC request -> call sequence -> observed divergence.
5. `Verifier::is_deposit_valid`, `Operator::is_profitable`, `SECP.verify_schnorr`, `only_aggregator_and_self`, `Verifier::is_kickoff_malicious`, `verify_storage_proofs`, `SPV::verify`, `lc_proof_verifier`, the presigned transaction graph and database constraints reviewed and shown insufficient.
6. Impact stated concretely: how much BTC or collateral moves, whose, and whether it is repeatable across deposits and operators.
7. Reproducible proof: `cargo test` with the asserted values, no mainnet, no live Citrea.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary depositor, withdrawer, Bitcoin broadcaster or internet user trigger it with no role and no key?
- Is the flaw in this repository's code, not in a dependency, the Citrea contract or a careless deployment?
- What value moves, whose collateral burns, or which proof becomes false, and is it repeatable?
- Would a Citrea bridge triager accept the exploit path?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken binding and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is spent, credited, frozen, proved or burned, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, configuration, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Clementine.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repository context only (`core/src/**`, `crates/*/src/**`, `circuits-lib/src/**`, `bridge-circuit-host/src/**`, excluding tests, mocks and generated files). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-attacker analogs that break a bridge-custody binding: value leaving a move-to-vault UTXO versus a withdrawal actually fronted, the operator credited versus the party that paid, a deposit presigned versus a deposit minted once, a block hash committed versus a block hash proved, a message signed versus a nonce reused, a caller reaching a signing or state-changing method versus the party it is meant for.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures, generated files (`core/src/rpc/clementine.rs`, `seal_format.rs`, `**/build.rs`, `elfs/`), `*.toml`, `*.md`, `docs/**`, `devops/**`, `scripts/**`, migrations; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; anything requiring a verifier, operator, watchtower, aggregator, security council or sequencer role, key or certificate, a malicious peer or node, key compromise, majority hashrate, TLS interception, local access or social engineering; third-party crate, Citrea contract or light-client-circuit defects with no path through this repository; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - BTC leaving a move-to-vault UTXO without a matching fronted withdrawal, an operator reimbursed for a payout it never funded, an honest operator permanently unable to be reimbursed, an honest operator's collateral burned, a vault UTXO permanently frozen, a false circuit claim proved or a true one made unprovable, unauthorised N-of-N partial signatures, or a recoverable verifier secret or secnonce; High - an unauthenticated state-changing or broadcasting call, a deadline-bound challenge/disprove/timeout transaction made unconfirmable, or premature disclosure of a protocol commitment.
- Reject analogs that depend on a deployment ignoring the documented configuration, and analogs with no custody, attribution, proof or authorization boundary crossed.

## Validate
- Map the bug class to the strongest reachable path in this repository and state the binding it would break as an equality.
- Evaluate both sides before and after the attacker's transaction or request sequence.
- Prove root cause with exact file/fn support.
- Accept only concrete bridge value loss, a misattributed or duplicated reimbursement, an honest operator slashed or frozen out, a broken circuit soundness claim, or an unauthorised signing or broadcast.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
