
"""Centralized run outcome evaluation."""

from agentguard.core.result import RunResult
from agentguard.core.manifest import ComparisonManifest
from agentguard.contracts.compatibility import supported_matrix


def evidence_errors(result: RunResult) -> list[str]:
    """Shared evidence integrity checks for run and comparison decisions."""
    problems = []
    if (any(event.tool_name=='model.generate' for event in result.audit) or result.execution_context is not None and result.execution_context.capabilities.supports_model_usage) and result.model_evidence is None:
        problems.append('Measured model usage evidence is missing.')
    if result.model_evidence is not None:
        try:
            from agentguard.model_gateway.gateway import GatewayEvidence
            if not result.model_evidence.get('fingerprint'):raise ValueError('Missing model usage fingerprint')
            model=GatewayEvidence.model_validate(result.model_evidence)
            if model.status!='passed':raise ValueError('Model usage is not settled within budget')
            if model.mode=='mock' and result.comparison_manifest is not None:
                from agentguard.evaluation.specs import requires_business_evidence
                if requires_business_evidence(result.comparison_manifest.scenario):raise ValueError('Mock model cannot verify business checks')
            judge_specs=[spec for spec in result.comparison_manifest.scenario.evaluations if spec.kind=='judge'] if result.comparison_manifest is not None else []
            if judge_specs and (model.mode!='live' or len([row for row in model.calls if row.purpose=='judge' and row.status=='completed'])!=sum(len(spec.config['labels'])+1 for spec in judge_specs)):raise ValueError('Judge calibration/call evidence is incomplete')
            if result.execution_context is not None and result.execution_context.execution_mode=='controlled':
                if len([row for row in model.calls if row.purpose=='agent'])!=len([event for event in result.audit if event.tool_name=='model.generate' and event.status!='denied']):raise ValueError('Model calls do not match controller audit')
                from agentguard.core.manifest import digest
                calls=[row for row in model.calls if row.purpose=='agent']
                events=[event for event in result.audit if event.tool_name=='model.generate' and event.status!='denied']
                for call,event in zip(calls,events):
                    prompt=dict(messages=event.arguments['messages'],system=model.config.system_prompt,version=model.config.prompt_version)
                    if call.prompt_digest!=digest(prompt):raise ValueError('Model request/usage binding mismatch')
        except (ValueError,TypeError,KeyError):problems.append('Model usage/budget evidence is unverified or inconsistent.')
    manifest_for_checks=result.comparison_manifest
    if manifest_for_checks is not None:
        try:
            from agentguard.evaluation.registry import EvaluationFinding
            from agentguard.core.manifest import digest,check_id
            specs=manifest_for_checks.scenario.evaluations
            if specs:
                from agentguard.core.paths import values_equal
                from agentguard.evaluation.diff import diff_states
                evidence=result.evaluation_evidence
                if set(evidence)!={'before','after','output','audit','turn_outputs'} or not values_equal(evidence['before'],manifest_for_checks.scenario.initial_state) or not values_equal(evidence['output'],result.worker_output) or not values_equal(evidence['audit'],[event.model_dump(mode='json') for event in result.audit]) or not values_equal(evidence['turn_outputs'],result.turn_outputs):raise ValueError('Recorded observations differ from evaluated evidence')
                if not values_equal([m.model_dump(mode='json') for m in diff_states(evidence['before'],evidence['after'])],[m.model_dump(mode='json') for m in result.mutations]):raise ValueError('Snapshot/mutation evidence differs')
            if {f.id for f in result.evaluations}!={s.id for s in specs} or len(result.evaluations)!=len(specs):raise ValueError('Evaluation coverage mismatch')
            assertions={a.assertion_id:a for a in result.assertions}
            for spec in specs:
                finding=next(f for f in result.evaluations if f.id==spec.id)
                EvaluationFinding.model_validate(finding.model_dump())
                assertion=assertions.get(check_id('evaluation',spec.id))
                if finding.spec_digest!=digest(spec.model_dump(mode='json')) or finding.evidence_digest!=digest(result.evaluation_evidence) or finding.kind!=spec.kind or finding.status!='pass' or assertion is None or not assertion.passed:raise ValueError('Evaluator missing, failed or inconclusive')
            if len(result.turn_outputs)!=len(manifest_for_checks.scenario.turns):raise ValueError('Declared session was not fully observed')
        except (ValueError,TypeError,KeyError):problems.append('Evaluator/session evidence is incomplete or inconsistent.')
    if result.generation_ref is not None or result.admission_status != "external":
        try:
            from agentguard.generation.evidence import ApprovalEvidence
            from agentguard.core.manifest import digest
            if result.admission_status != "approved" or result.generation_ref is None or result.approval_evidence is None:
                raise ValueError("Generated case is not approved for release")
            receipt = ApprovalEvidence.model_validate(result.approval_evidence)
            reference = result.generation_ref
            if reference != dict(case_id=receipt.case_id,revision=receipt.revision,candidate_fingerprint=receipt.candidate_fingerprint,scenario_fingerprint=receipt.scenario_fingerprint):
                raise ValueError("Approval/case binding mismatch")
            manifest = result.comparison_manifest
            if manifest is None or manifest.fingerprint != receipt.comparison_fingerprint:
                raise ValueError("Approved comparison context changed")
            if manifest is None or manifest.scenario.generation != reference or digest(manifest.scenario.model_dump(mode="json",exclude={"generation"})) != receipt.scenario_fingerprint:
                raise ValueError("Generated scenario content changed after approval")
        except (ValueError,TypeError,KeyError):
            problems.append("Generated scenario release admission is missing, stale or inconsistent.")
    if result.integration_status in {"unverified", "unsupported"}:
        problems.append("Integration evidence is not verified.")
    if result.integration_status == "verified":
        try:
            from agentguard.integrations.evidence import IntegrationEvidence
            evidence = IntegrationEvidence.model_validate(result.integration_manifest)
            context = result.execution_context
            expected_adapter = {'http':'http.endpoint','python':'python.container','container':'container.ipc'}[evidence.agent_transport]
            if context is None or context.adapter.id != expected_adapter:
                problems.append("Integration agent/assurance identity is inconsistent.")
            comparison = result.comparison_manifest
            environment = comparison.environment if comparison is not None else ''
            expected = 'integration:' + evidence.fingerprint if evidence.agent_transport == 'http' else 'controlled.simulator.v1:' + evidence.fingerprint + ':'
            if (environment != expected if evidence.agent_transport == 'http' else not environment.startswith(expected)):
                problems.append("Integration and comparison manifests are not bound.")
            if 'mock' in evidence.modes and comparison is not None:
                scenario = comparison.scenario
                from agentguard.evaluation.specs import requires_business_evidence
                if requires_business_evidence(scenario):
                    problems.append("Schema mock cannot establish business state/effect evidence.")
        except (ValueError, TypeError, KeyError):
            problems.append("Integration manifest is invalid or was modified.")
    context = result.execution_context
    if context is not None:
        profile = next((row for row in supported_matrix()
                        if row.component == "agent" and row.adapter == context.adapter
                        and row.implementation == "implemented"), None)
        if profile is None or context.execution_mode not in profile.execution_modes:
            problems.append("Execution context claims an unsupported adapter or assurance mode.")
        elif any(not getattr(profile.capabilities, name) for name in context.capabilities.supported()):
            problems.append("Execution context claims unsupported capabilities.")
    if result.execution_context is not None and result.execution_context.execution_mode == "controlled":
        manifest = result.run_manifest
        if result.cleanup_status != "verified": problems.append("Controlled cleanup is not verified.")
        if result.policy_status != "passed": problems.append("Controlled tool policy did not pass.")
        if manifest is None or not manifest.started or not manifest.completed or not all((manifest.image_id, manifest.source_digest, manifest.base_digest, manifest.worker_digest, manifest.container_id)):
            problems.append("Controlled runtime evidence is incomplete.")
        if any(event.status != "completed" and not _verified_injected_recovery(result,event) for event in result.audit):
            problems.append("Controlled audit contains denied or failed calls.")
    names = [item.name for item in result.assertions]
    ids = [item.assertion_id for item in result.assertions if item.assertion_id is not None]
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        problems.append("Duplicate assertion identity.")
    paths = [item.path for item in result.mutations]
    if len(paths) != len(set(paths)):
        problems.append("Duplicate mutation paths.")
    if result.comparison_manifest is not None:
        try:
            manifest = ComparisonManifest.model_validate(result.comparison_manifest.model_dump())
            required, observed = set(manifest.check_definitions()), set(ids)
            if manifest.scenario.name != result.scenario_name:
                problems.append("Scenario name does not match the manifest.")
            if result.evaluation_status == "complete" and observed != required:
                problems.append("Completed evaluation does not contain exactly the manifest checks.")
            if observed - required:
                problems.append("Unexpected assertion identities.")
            if any(item.assertion_id is None and item.name != "agent:execution" for item in result.assertions):
                problems.append("Manifest assertion identity is missing.")
        except ValueError:
            problems.append("Comparison manifest is invalid or was modified.")
    return problems

def _verified_injected_recovery(result,event):
    """Only explicit no-effect injected faults with a passing recovery check are exempt."""
    scenario=result.comparison_manifest.scenario if result.comparison_manifest is not None else None
    if scenario is None or event.status!='error' or event.effect_status!='none':return False
    fault=next((fault for fault in scenario.faults if fault.id==event.fault_id and fault.tool_name==event.tool_name and event.reason=='INJECTED_'+fault.kind.upper()),None)
    if fault is None:return False
    prior=[e for e in result.audit if e.sequence<=event.sequence and e.tool_name==event.tool_name]
    if len(prior)!=fault.call_index:return False
    checks={check.id for check in scenario.evaluations if check.kind=='fault_recovery' and check.config['fault_id']==fault.id}
    return any(f.id in checks and f.status=='pass' for f in result.evaluations) and any(e.sequence>event.sequence and e.tool_name==event.tool_name and e.status=='completed' for e in result.audit)


def effective_pass(result: RunResult) -> bool:
    """
    Return True only when a run is safe to classify as PASS.

    Cleanup verification is required when cleanup is applicable.
    """
    return (
        result.passed is True
        and result.status == "passed"
        and result.evaluation_status == "complete"
        and result.error_type is None
        and result.error_message is None
        and result.error_stage is None
        and result.evaluation_error_type is None
        and result.evaluation_error_message is None
        and result.cleanup_status in {
            "not_required",
            "verified",
        }
        and bool(result.assertions)
        and all(assertion.passed for assertion in result.assertions)
        and not evidence_errors(result)
    )
