import pytest
pytestmark=pytest.mark.unit

def test_phase8_environment_catalog_advertises_only_selected_controlled_profiles():
    from agentguard.contracts.compatibility import supported_matrix
    rows={row.adapter.id:row for row in supported_matrix()}
    for kind in ['browser','database','inbox','calendar','clock']:
        row=rows[kind+'.fixture']
        assert row.implementation=='implemented' and row.environment_kinds==[kind]
        assert row.execution_modes==['controlled'] and row.capabilities.supports_state_snapshot and row.capabilities.supports_reset
