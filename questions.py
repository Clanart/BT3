import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "starkware-libs/sequencer"
REPO_NAME = "sequencer"
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    "crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs",
    "crates/apollo_batcher/src/commitment_manager/state_committer.rs",
    "crates/apollo_batcher/src/pre_confirmed_cende_client.rs",
    "crates/apollo_central_sync/src/pending_sync.rs",
    "crates/apollo_central_sync/src/sources/base_layer.rs",
    "crates/apollo_central_sync/src/sources/central.rs",
    "crates/apollo_central_sync/src/sources/central/state_update_stream.rs",
    "crates/apollo_central_sync/src/sources/pending.rs",
    "crates/apollo_class_manager/src/class_storage.rs",
    "crates/apollo_committer/src/committer.rs",
    "crates/apollo_consensus_orchestrator/src/cende/central_objects.rs",
    "crates/apollo_gateway/src/proof_archive_writer.rs",
    "crates/apollo_gateway/src/state_reader.rs",
    "crates/apollo_l1_provider/src/catchupper.rs",
    "crates/apollo_l1_provider/src/l1_provider.rs",
    "crates/apollo_l1_provider/src/l1_scraper.rs",
    "crates/apollo_l1_provider/src/transaction_manager.rs",
    "crates/apollo_l1_provider/src/transaction_record.rs",
    "crates/apollo_p2p_sync/src/client/block_data_stream_builder.rs",
    "crates/apollo_p2p_sync/src/client/class.rs",
    "crates/apollo_p2p_sync/src/client/header.rs",
    "crates/apollo_p2p_sync/src/client/state_diff.rs",
    "crates/apollo_p2p_sync/src/client/transaction.rs",
    "crates/apollo_p2p_sync/src/server/mod.rs",
    "crates/apollo_p2p_sync/src/server/utils.rs",
    "crates/apollo_proof_manager/src/proof_manager.rs",
    "crates/apollo_proof_manager/src/proof_storage.rs",
    "crates/apollo_rpc/src/pending.rs",
    "crates/apollo_rpc/src/syncing_state.rs",
    "crates/apollo_state_reader/src/apollo_state.rs",
    "crates/apollo_state_sync/src/runner/mod.rs",
    "crates/apollo_state_sync_types/src/lib.rs",
    "crates/apollo_storage/src/base_layer.rs",
    "crates/apollo_storage/src/block_hash.rs",
    "crates/apollo_storage/src/body/events.rs",
    "crates/apollo_storage/src/body/mod.rs",
    "crates/apollo_storage/src/class.rs",
    "crates/apollo_storage/src/class_hash.rs",
    "crates/apollo_storage/src/class_manager.rs",
    "crates/apollo_storage/src/compiled_class.rs",
    "crates/apollo_storage/src/compression_utils.rs",
    "crates/apollo_storage/src/consensus.rs",
    "crates/apollo_storage/src/db/mod.rs",
    "crates/apollo_storage/src/db/serialization.rs",
    "crates/apollo_storage/src/deprecated/migrations.rs",
    "crates/apollo_storage/src/deprecated/serializers.rs",
    "crates/apollo_storage/src/global_root.rs",
    "crates/apollo_storage/src/global_root_marker.rs",
    "crates/apollo_storage/src/header.rs",
    "crates/apollo_storage/src/partial_block_hash.rs",
    "crates/apollo_storage/src/serialization/mod.rs",
    "crates/apollo_storage/src/serialization/serializers.rs",
    "crates/apollo_storage/src/state/data.rs",
    "crates/apollo_storage/src/state/mod.rs",
    "crates/apollo_storage/src/storage_reader_server.rs",
    "crates/apollo_storage/src/storage_reader_types.rs",
    "crates/blockifier/src/state/cached_state.rs",
    "crates/blockifier/src/state/contract_class_manager.rs",
    "crates/blockifier/src/state/state_api.rs",
    "crates/blockifier/src/state/state_reader_and_contract_manager.rs",
    "crates/blockifier/src/state/stateful_compression.rs",
    "crates/papyrus_base_layer/src/cyclic_base_layer_wrapper.rs",
    "crates/papyrus_base_layer/src/eth_events.rs",
    "crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs",
    "crates/papyrus_common/src/pending_classes.rs",
    "crates/papyrus_common/src/state.rs",
    "crates/starknet_api/src/block_hash/block_hash_calculator.rs",
    "crates/starknet_api/src/block_hash/event_commitment.rs",
    "crates/starknet_api/src/block_hash/receipt_commitment.rs",
    "crates/starknet_api/src/block_hash/state_diff_hash.rs",
    "crates/starknet_api/src/block_hash/transaction_commitment.rs",
    "crates/starknet_api/src/class_cache.rs",
    "crates/starknet_api/src/compression_utils.rs",
    "crates/starknet_api/src/data_availability.rs",
    "crates/starknet_api/src/state.rs",
    "crates/starknet_committer/src/block_committer/commit.rs",
    "crates/starknet_committer/src/block_committer/input.rs",
    "crates/starknet_committer/src/block_committer/state_diff_generator.rs",
    "crates/starknet_committer/src/db/facts_db/create_facts_tree.rs",
    "crates/starknet_committer/src/db/facts_db/db.rs",
    "crates/starknet_committer/src/db/facts_db/node_serde.rs",
    "crates/starknet_committer/src/db/facts_db/traversal.rs",
    "crates/starknet_committer/src/db/index_db/db.rs",
    "crates/starknet_committer/src/db/index_db/leaves.rs",
    "crates/starknet_committer/src/db/trie_traversal.rs",
    "crates/starknet_committer/src/forest/filled_forest.rs",
    "crates/starknet_committer/src/forest/original_skeleton_forest.rs",
    "crates/starknet_committer/src/forest/updated_skeleton_forest.rs",
    "crates/starknet_committer/src/hash_function/hash.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/leaf/leaf_impl.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/leaf/leaf_serde.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/tree.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/types.rs",
    "crates/starknet_os/src/commitment_infos.rs",
    "crates/starknet_os/src/io/os_input.rs",
    "crates/starknet_os/src/io/os_output.rs",
    "crates/starknet_os/src/io/os_output_types.rs",
    "crates/starknet_os/src/io/virtual_os_output.rs",
    "crates/starknet_os/src/runner.rs",
    "crates/starknet_patricia/src/db_layout.rs",
    "crates/starknet_patricia/src/felt.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/filled_tree/tree.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/node_data/inner_node.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/node_data/leaf.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/original_skeleton_tree/tree.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/traversal.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/types.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/updated_skeleton_tree/tree.rs",
    "crates/starknet_patricia_storage/src/aerospike_storage.rs",
    "crates/starknet_patricia_storage/src/map_storage.rs",
    "crates/starknet_patricia_storage/src/mdbx_storage.rs",
    "crates/starknet_patricia_storage/src/rocksdb_storage.rs",
    "crates/starknet_patricia_storage/src/storage_trait.rs",
    "crates/starknet_proof_verifier/src/proof_verifier.rs",
    "crates/starknet_transaction_prover/src/proving/prover.rs",
    "crates/starknet_transaction_prover/src/running/classes_provider.rs",
    "crates/starknet_transaction_prover/src/running/committer_utils.rs",
    "crates/starknet_transaction_prover/src/running/runner.rs",
    "crates/starknet_transaction_prover/src/running/storage_proofs.rs",
    "crates/starknet_transaction_prover/src/running/virtual_block_executor.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered state diff, Patricia trie, committer, storage proof, or global-root bug commits or verifies the wrong Starknet state root.",
    "Critical. Unprivileged-user-triggered block hash, transaction commitment, event commitment, receipt commitment, data-availability, or CENDE path binds valid-looking block data to the wrong commitment.",
    "Critical. Unprivileged-user-triggered state sync, central sync, p2p sync, pending sync, or L1 provider path installs or serves state/classes/transactions for the wrong block, root, height, or L1 event.",
    "Critical. Unprivileged-user-triggered proof manager, transaction prover, SNOS input/output, or storage proof path proves a block, class, storage read, or execution result against mismatched data.",
    "High. Unprivileged-user-triggered storage serialization, migration, compression, or deprecated storage path returns stale, duplicated, skipped, or cross-table data to execution, sync, or RPC.",
    "High. Unprivileged-user-triggered pending block/class/state cache path leaks uncommitted or cross-block data into authoritative RPC, execution, proof, or sync flows.",
    "High. Unprivileged-user-triggered L1 event scraper/provider ordering or reorg handling makes the sequencer consume, skip, duplicate, or misattribute L1 messages with state/proof impact.",
]

EXECUTION_ALLOWED_IMPACT_SCOPE = """## Allowed Impact Scope
Only these impacts are valid:
- Critical. Invalid or unauthorized Starknet transaction accepted through account validation, signature, nonce, chain id, fee/resource bound, paymaster, or account-deployment logic.
- Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.
- Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.
- Critical. Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution.
- High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.
- High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.
- High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."""

SMART_AUDIT_PIVOTS = """## Sequencer-Specific Audit Pivots
- Commitment construction: `BlockExecutionArtifacts::new` converts `CommitmentStateDiff` to `ThinStateDiff`, calls `calculate_block_commitments`, records `state_diff_length`, and derives `PartialBlockHashComponents`. Audit ordering and equality between execution outputs, signatures, state diff, commitments, and final proposal commitment.
- Block hash/root path: `calculate_block_hash` chains versioned prefix, block number, global root, sequencer, timestamp, concatenated counts, state-diff/tx/event/receipt commitments, gas prices, Starknet version, zero field, and parent hash. For partial hashes, global root and parent hash are deliberately zero; questions must prove a wrong accepted commitment, not just notice that design.
- Storage/proof path: storage serializers cover headers, transactions, state diffs, commitments, classes, statuses, DA modes, resource bounds, proof facts, and deprecated objects. Look for cross-table/key/version mismatches that feed execution, sync, RPC, proof manager, or transaction prover with a wrong authoritative value.
- Sync/prover path: state sync/central sync/L1 provider data, proof-manager storage, SNOS input/output, and transaction prover storage proofs must agree on block number, state root, class hash, storage key, proof facts hash, L1 event identity, and pending markers."""


def question_generator(target_file: str) -> str:
    """
    Generate state, proof, and data-commitment questions for one target.
    """

    prompt = f"""
    Generate state/proof/data-commitment security questions for this exact Starknet Sequencer target:

    {target_file}

    Lens:
    Focus on whether Sequencer code stores, reconstructs, proves, syncs, and serves the exact data committed by Starknet protocol objects. Look at state diffs, Patricia tries, global roots, block hashes, transaction/event/receipt commitments, CENDE data, state sync, pending data, L1 events, SNOS inputs/outputs, proof storage, and transaction proving.

    Execution/admission impact gate:
    {EXECUTION_ALLOWED_IMPACT_SCOPE}

    {SMART_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * Attacker is unprivileged: public RPC client, ordinary account/contract user, low-trust peer, or source of publicly consumed L1/L2 data.
    * Do not grant state-sync provider operator, sequencer operator, validator/proposer, oracle, node admin, database, storage-service, or deployment privileges unless the question proves an unprivileged bypass.
    * Malicious-peer-only/provider-only behavior is out of scope when bad data is rejected, ignored, disconnected, retried, rate-limited, or only wastes resources.
    * Bad data that is rejected, ordinary DoS, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, performance-only degradation, tests, mocks, benches, generated data, scripts, deployments, and local tooling are out of scope unless one allowed impact above or the target Scope is concretely reached.
    * Generate 16 to 22 high-signal questions, mostly crossing storage/sync/proof/execution boundaries.
    * Name the exact value at risk: state root, global root, block hash, state diff, class hash, storage key/value, commitment leaf, proof fact, SNOS input/output, block number, L1 event id, pending marker, storage row, or RPC/sync result.
    * Every question must be testable with a Rust unit/property/fuzz test, proof/state-sync test, or focused local reproducer.

    Each question must include target symbol, attacker-controlled data, preconditions, call path, commitment invariant, exact corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled DATA under PRECONDITIONS pass CALL_PATH and violate COMMITMENT_OR_PROOF_INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a Rust state/proof/property reproducer over PARAMETERS and assert EXPECTED_COMMITMENT_BINDING.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a state/proof question validation prompt.
    """
    return f"""# STATE AND PROOF QUESTION REVIEW

## Question
{question}

## Boundary
Audit only production Sequencer files listed in `scope_files`. Ignore tests, mocks, fixtures, generated data, docs, benches, scripts, deployments, and local tools.

## Goal
Decide whether the question can expose a reachable bug in state reconstruction, commitment binding, storage, sync, pending data, L1 event ordering, SNOS input/output, proof production, or proof verification.

A valid path must show unprivileged-controlled data causing production code to accept, store, prove, sync, or serve the wrong committed value. Prefer #NoVulnerability unless the exact corrupted root/hash/proof/storage/sync value is concrete.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Review Steps
1. Identify the production entrypoint and target symbol.
2. Bind attacker data to block number, state root, block hash, class hash, trie path, L1 event, proof fact, or storage key.
3. Trace validation, serialization, storage, proof, sync, and RPC decisions.
4. Check existing hash/root/proof/order/cache/table guards.
5. Reject if guards prevent the mismatch or impact is resource-only, unbounded-growth-only, or malicious-peer-only.

## Fast Rejections
- Requires operator/admin/validator/proposer/oracle/database/storage-service privileges.
- Bad peer/provider data is rejected, ignored, retried, disconnected, or only wastes resources.
- Ordinary crash, DoS, timeout, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, performance-only degradation, logging, style, dependency-only behavior.
- No exact corrupted committed-data value or no unprivileged path.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate an analog scan prompt for state/proof issues.
    """
    prompt = f"""# STATE AND PROOF ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a seed for a Sequencer-native analog in state roots, Patricia tries, block commitments, state diffs, storage serialization, pending data, L1 events, state sync, proof inputs/outputs, or transaction proving.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

Report only if this repo has its own reachable root cause, unprivileged trigger, broken commitment/proof invariant, exact corrupted value, and matching target scope or one of the impacts above. Reject privileged operations, malicious-peer/provider-only noise, resource-only issues, unbounded growth, dependency-only behavior, and non-production files.

## Work Plan
1. Translate the external bug into a root, commitment, ordering, cache-key, serialization, or proof invariant.
2. Map it to exact production symbols.
3. Trace attacker-controlled data through validation and storage/proof/sync.
4. Identify the wrong state root, block hash, commitment, state diff, class hash, storage value, proof fact, SNOS value, L1 event, pending marker, or RPC/sync result.
5. Reject if existing checks preserve the invariant.

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


def validation_format(report: str) -> str:
    """
    Generate a strict state/proof validation prompt.
    """
    prompt = f"""# STATE AND PROOF VALIDATION

## Security Claim
{report}

## Validation Rules
- Validate only this claim against production Sequencer files in `scope_files`.
- A valid issue must be reachable through unprivileged public RPC, transaction/contract, low-trust peer, state-sync, pending-data, or L1/L2 data paths.
- Reject operator/admin/validator/proposer/oracle/database/storage-service assumptions, bad data that is rejected, malicious-peer-only behavior, ordinary crash/DoS, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, tests/mocks/generated files, docs, scripts, deployment tooling, dependency-only bugs, and downstream misuse.
- The final impact must match one allowed scope below or one execution/admission impact, and name the exact corrupted value.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Wrong Starknet state root accepted, committed, proved, or served due to state diff, Patricia trie, committer, storage proof, or global-root logic.
- Critical. Wrong block hash, transaction/event/receipt commitment, or data-availability binding accepted for valid-looking block data.
- Critical. State sync, central sync, p2p sync, pending sync, or L1 provider installs or serves data for the wrong block/root/height/L1 event.
- Critical. Proof manager, transaction prover, SNOS input/output, or storage proof proves mismatched block/class/storage/execution data.
- High. Storage serialization, migration, compression, or deprecated table path returns stale, skipped, duplicated, or cross-table data to production consumers.
- High. Pending state/class/block cache leaks uncommitted or cross-block data into authoritative flows.
- High. L1 event ordering or reorg handling consumes, skips, duplicates, or misattributes L1 messages with state/proof impact.

## Required Checks
1. Exact file/function/line references.
2. Broken root, commitment, ordering, cache-key, storage, serialization, sync, or proof invariant.
3. Exploit path: preconditions -> attacker data -> call path -> bad committed value.
4. Existing guards shown insufficient.
5. Reproducible Rust test, property/fuzz test, state-sync/proof test, or local reproducer.

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary]

## Finding Description
[Code path, root cause, exploit flow, and failed guards]

## Impact Explanation
[Concrete allowed impact and severity]

## Likelihood Explanation
[Attacker capability and conditions]

## Recommendation
[Specific fix]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
